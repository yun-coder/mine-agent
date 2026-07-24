"""终端命令安全沙箱 / Terminal command security sandbox.

三层防御 / Three-layer defence:
  1. 命令黑名单 / Command blacklist  — 被拦截的模式 / blocked patterns
  2. 路径白名单 / Path allowlist     — 仅允许的目录 / only permitted directories
  3. 执行隔离 / Execution isolation — subprocess(cwd=…, timeout=…, shell=False)
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from src.config import settings

# ------------------------------------------------------------------
# 1. 命令黑名单 / Command blacklist
# ------------------------------------------------------------------

BLOCKED_PATTERNS: list[str] = [
    # 破坏性操作 / Destructive
    r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f",
    r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\bshred\b",
    r">\s*/dev/(sd|nvme|vd)",
    # 提权 / Privilege escalation
    r"\bsudo\b",
    r"\bsu\s+",
    r"\bsudo\s",
    r"\bsudo\b",
    # 危险的 chmod/chown / Dangerous chmod/chown
    r"\bchmod\s+-?R",
    r"\bchmod\s+777",
    r"\bchown\s+-?R",
    # 代码执行 / Code execution
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"\bsource\s+",
    # 网络外泄 / Network exfiltration
    r"\bwget\s+http",
    r"\bcurl\s+.*-X\s+(POST|PUT|DELETE|PATCH)",
    r"\bcurl\s+.*-d\s+",
    r"\bnc\s+-[a-zA-Z]*e\b",
    r"\bnetcat\s+-[a-zA-Z]*e\b",
    r"\bbash\s+-i\b",
    r"\bpowershell\s+-[a-zA-Z]*e\b",
    r"\bpowershell\s+-command\b",
    r"\b(cmd|powershell)\s+.*\|",
    # 文件系统破坏 / Filesystem sabotage
    r">\s*/etc/",
    r"\b/etc/shadow\b",
    r"\b/etc/passwd\b",
    r"\b/etc/sudoers\b",
    # Shell 逃逸 / Shell escapes
    r"\|?\s*(bash|sh|zsh|csh|ksh|cmd|powershell)\b",
    r"&&\s*(bash|sh|zsh|csh|ksh)",
    # 数据销毁 / Data destruction
    r"\bfdisk\b",
    r"\bparted\b",
    r"\bwipe\b",
]

_BLOCKED_RE = re.compile("|".join(BLOCKED_PATTERNS), re.IGNORECASE)

# ------------------------------------------------------------------
# 2. 路径白名单 / Path allowlist
# ------------------------------------------------------------------

_DEFAULT_ALLOWLIST: list[Path] = [
    Path(tempfile.gettempdir()),                          # 平台临时目录 / platform temp
]

# 从环境变量读取允许目录 / Read allowed dirs from env
_docs_dir = os.environ.get("DOCS_DIR", "").strip()
if _docs_dir:
    _DEFAULT_ALLOWLIST.append(Path(_docs_dir))

_project_root = os.environ.get("PROJECT_ROOT", "").strip()
if _project_root:
    _DEFAULT_ALLOWLIST.append(Path(_project_root))
elif settings.project_root:
    _DEFAULT_ALLOWLIST.append(settings.project_root)

# 同时在 Linux/Mac 上允许 /tmp 目录 / Also allow /tmp on Linux/Mac for compatibility
if os.name != "nt":
    _DEFAULT_ALLOWLIST.append(Path("/tmp"))
    _DEFAULT_ALLOWLIST.append(Path("/var/tmp"))

ALLOWED_DIRS: list[Path] = _DEFAULT_ALLOWLIST


def set_allowed_dirs(directories: list[str] | list[Path]) -> None:
    """在运行时覆盖默认的允许目录。/ Override the default allowed directories at runtime."""
    global ALLOWED_DIRS
    ALLOWED_DIRS = [Path(d) for d in directories]


# ------------------------------------------------------------------
# 3. 安全检查器 / 3. Sanitiser
# ------------------------------------------------------------------


def extract_paths(command: str) -> list[str]:
    """从 shell 命令字符串中粗略提取路径。/ Very rough path extraction from a shell command string."""
    paths: list[str] = []
    # Windows 风格路径 / Windows style
    paths.extend(re.findall(r'[A-Za-z]:[\\/][^\s;"\'|&<>]*', command))
    # POSIX 风格路径 / POSIX style
    paths.extend(re.findall(r'(?:^|\s)(/[^\s;"\'|&<>]+)', command))
    return paths


def terminal_sanitizer(command: str) -> dict[str, Any]:
    """根据安全规则校验终端命令。/ Validate a terminal command against safety rules.

    返回 / Returns:
        {"safe": True,  "command": str}          — 允许 / allowed
        {"safe": False, "reason": str}           — 拦截并说明原因 / blocked with explanation
    """
    stripped = command.strip()
    if not stripped:
        return {"safe": False, "reason": "空命令 / Empty command"}

    # --- 第一层：命令黑名单 / --- Layer 1: command blacklist ---
    m = _BLOCKED_RE.search(stripped)
    if m:
        reason = f"拦截的命令模式 / Blocked command pattern: {m.group()}"
        logger.warning(f"[沙箱 / Sanitizer] 拦截 / blocked: {reason} — 输入={stripped[:200]}")
        return {"safe": False, "reason": reason}

    # --- 第二层：路径白名单 / --- Layer 2: path allowlist ---
    cmd_paths = extract_paths(stripped)
    for raw_path in cmd_paths:
        try:
            resolved = Path(raw_path).resolve()
        except OSError:
            continue
        # 忽略 /dev/*（只读设备文件，通常 ls /dev 没问题）/ Ignore /dev/… (read-only device files — generally OK for `ls /dev`)
        if str(resolved).startswith("/dev"):
            continue
        if not any(
            resolved.is_relative_to(d.resolve() if d.is_dir() else d)
            for d in ALLOWED_DIRS
        ):
            reason = f"路径不在白名单内 / Path outside allowlist: {raw_path}"
            logger.warning(f"[沙箱 / Sanitizer] 路径拦截 / path blocked: {reason} — 命令={stripped[:200]}")
            return {"safe": False, "reason": reason}

    # --- 第三层：管道到 Shell 解释器 / --- Layer 3: pipe to shell interpreter ---
    if re.search(r"\|\s*(bash|sh|zsh|csh|ksh|cmd|powershell)\b", stripped, re.IGNORECASE):
        reason = "管道到 Shell 解释器 / Piped to shell interpreter"
        logger.warning(f"[沙箱 / Sanitizer] {reason} — 命令={stripped[:200]}")
        return {"safe": False, "reason": reason}

    return {"safe": True, "command": stripped}
