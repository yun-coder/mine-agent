"""工具系统 —— Agent 可调用的主机工具（文件系统、Shell 等）。

每个工具定义其名称、描述、参数模式，执行时受安全检查约束。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional


# ─── 工具定义 ─────────────────────────────────────────────────────

@dataclass
class ToolParam:
    """工具参数的定义。"""
    name: str
    type: str  # string | integer | boolean | array
    description: str
    required: bool = True
    default: Any = None


@dataclass
class ToolDefinition:
    """工具的完整定义（发送给 LLM 使用）。"""
    name: str
    description: str
    parameters: list[ToolParam] = field(default_factory=list)
    handler: Optional[Callable] = None

    def to_llm_format(self) -> dict:
        """转换为 LLM function-calling 格式。"""
        properties = {}
        required = []
        for p in self.parameters:
            prop = {"type": p.type, "description": p.description}
            if p.default is not None:
                prop["default"] = p.default
            properties[p.name] = prop
            if p.required:
                required.append(p.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }


@dataclass
class ToolResult:
    """工具执行的结果。"""
    success: bool
    output: str = ""
    error: str = ""
    data: Any = None


# ─── 主机文件系统工具 ──────────────────────────────────────────────

class FileSystemTools:
    """Agent 访问主机文件系统的安全工具集。

    所有文件路径都经过安全检查，防止路径穿越攻击。
    默认限制在用户主目录和临时目录。
    """

    # 允许访问的根目录（用于沙箱）
    ALLOWED_ROOTS: list[Path] = [
        Path.home(),
        Path("/tmp"),
        Path.home() / "Desktop",
        Path.home() / "Documents",
        Path.home() / "Downloads",
    ]

    # Windows 下额外允许的路径
    _WINDOWS_EXTRA = [
        Path(os.environ.get("USERPROFILE", "")) / "Desktop",
        Path(os.environ.get("USERPROFILE", "")) / "Documents",
        Path(os.environ.get("USERPROFILE", "")) / "Downloads",
        Path(os.environ.get("TEMP", "C:\\temp")),
    ]

    def __init__(self, extra_allowed_dirs: Optional[list[str]] = None):
        self.allowed_roots = list(self.ALLOWED_ROOTS)
        # Windows 额外路径
        for p in self._WINDOWS_EXTRA:
            if p not in self.allowed_roots:
                self.allowed_roots.append(p)
        # 用户自定义额外路径
        if extra_allowed_dirs:
            for d in extra_allowed_dirs:
                p = Path(d).resolve()
                if p.exists() and p not in self.allowed_roots:
                    self.allowed_roots.append(p)

    def _resolve_and_check(self, path: str) -> Path:
        """解析路径并验证是否在允许范围内。"""
        resolved = Path(path).resolve()
        allowed = False
        for root in self.allowed_roots:
            try:
                resolved.relative_to(root)
                allowed = True
                break
            except ValueError:
                continue
        if not allowed:
            raise PermissionError(
                f"路径 '{path}' 不在允许的访问范围内。\n"
                f"允许的根目录: {[str(r) for r in self.allowed_roots]}"
            )
        return resolved

    # ── 工具定义列表（供 LLM 使用） ───────────────────────────

    @classmethod
    def get_tool_definitions(cls) -> list[ToolDefinition]:
        """返回所有文件工具的 LLM 函数定义。"""
        return [
            ToolDefinition(
                name="read_file",
                description="读取指定文件的内容。支持 txt、py、js、ts、json、yaml、md、html、css、csv、log 等文本文件。",
                parameters=[
                    ToolParam(name="path", type="string", description="要读取的文件路径（绝对路径或相对路径）"),
                    ToolParam(name="encoding", type="string", description="文件编码，默认 utf-8", required=False, default="utf-8"),
                    ToolParam(name="max_lines", type="integer", description="最多读取的行数，默认全部读取", required=False, default=0),
                ],
                handler=cls._handle_read_file,
            ),
            ToolDefinition(
                name="write_file",
                description="写入内容到指定文件。如果文件不存在则创建，存在则覆盖。",
                parameters=[
                    ToolParam(name="path", type="string", description="要写入的文件路径（绝对路径）"),
                    ToolParam(name="content", type="string", description="文件内容"),
                    ToolParam(name="encoding", type="string", description="文件编码，默认 utf-8", required=False, default="utf-8"),
                ],
                handler=cls._handle_write_file,
            ),
            ToolDefinition(
                name="append_file",
                description="追加内容到指定文件末尾。如果文件不存在则创建。",
                parameters=[
                    ToolParam(name="path", type="string", description="要追加的文件路径（绝对路径）"),
                    ToolParam(name="content", type="string", description="要追加的内容"),
                    ToolParam(name="encoding", type="string", description="文件编码，默认 utf-8", required=False, default="utf-8"),
                ],
                handler=cls._handle_append_file,
            ),
            ToolDefinition(
                name="list_directory",
                description="列出指定目录的内容（文件和子目录）。",
                parameters=[
                    ToolParam(name="path", type="string", description="目录路径（绝对路径）"),
                    ToolParam(name="recursive", type="boolean", description="是否递归列出子目录", required=False, default=False),
                    ToolParam(name="pattern", type="string", description="文件名过滤模式（glob 模式，如 *.py）", required=False, default=""),
                ],
                handler=cls._handle_list_directory,
            ),
            ToolDefinition(
                name="file_info",
                description="获取文件或目录的元信息（大小、修改时间、类型等）。",
                parameters=[
                    ToolParam(name="path", type="string", description="文件或目录的路径（绝对路径）"),
                ],
                handler=cls._handle_file_info,
            ),
            ToolDefinition(
                name="search_files",
                description="在指定目录中搜索匹配模式的文件。",
                parameters=[
                    ToolParam(name="pattern", type="string", description="搜索模式（glob 模式，如 **/*.py）"),
                    ToolParam(name="root_dir", type="string", description="搜索的根目录（绝对路径），默认为用户主目录", required=False, default=""),
                    ToolParam(name="max_results", type="integer", description="最大返回结果数", required=False, default=50),
                ],
                handler=cls._handle_search_files,
            ),
            ToolDefinition(
                name="create_directory",
                description="创建目录（包括父目录）。",
                parameters=[
                    ToolParam(name="path", type="string", description="要创建的目录路径（绝对路径）"),
                ],
                handler=cls._handle_create_directory,
            ),
            ToolDefinition(
                name="delete_file",
                description="删除文件或空目录。注意：此操作不可逆！",
                parameters=[
                    ToolParam(name="path", type="string", description="要删除的文件或空目录路径（绝对路径）"),
                    ToolParam(name="recursive", type="boolean", description="是否递归删除（仅用于目录）", required=False, default=False),
                ],
                handler=cls._handle_delete,
            ),
            ToolDefinition(
                name="execute_command",
                description="在主机上执行一个 Shell 命令。以安全性为首要考虑，只允许读取和文件操作类命令。默认超时 30 秒。",
                parameters=[
                    ToolParam(name="command", type="string", description="要执行的 Shell 命令"),
                    ToolParam(name="timeout", type="integer", description="超时时间（秒）", required=False, default=30),
                    ToolParam(name="work_dir", type="string", description="工作目录（绝对路径）", required=False, default=""),
                ],
                handler=cls._handle_execute_command,
            ),
        ]

    # ── 工具处理函数（实例方法） ────────────────────────────

    async def _handle_read_file(self, task: dict) -> ToolResult:
        path = task.get("path", "")
        encoding = task.get("encoding", "utf-8")
        max_lines = task.get("max_lines", 0)
        try:
            resolved = self._resolve_and_check(path)
            if not resolved.exists():
                return ToolResult(success=False, error=f"文件不存在: {path}")
            if not resolved.is_file():
                return ToolResult(success=False, error=f"路径不是文件: {path}")

            if max_lines > 0:
                lines = []
                with open(resolved, "r", encoding=encoding) as f:
                    for i, line in enumerate(f):
                        if i >= max_lines:
                            break
                        lines.append(line)
                content = "".join(lines)
            else:
                content = resolved.read_text(encoding=encoding)

            size = resolved.stat().st_size
            return ToolResult(
                success=True,
                output=f"成功读取文件 ({size} 字节, {len(content.splitlines())} 行):\n{content}",
                data={"content": content, "size": size, "lines": len(content.splitlines())},
            )
        except PermissionError as e:
            return ToolResult(success=False, error=str(e))
        except UnicodeDecodeError:
            return ToolResult(success=False, error=f"文件不是文本文件或编码不是 {encoding}: {path}")
        except Exception as e:
            return ToolResult(success=False, error=f"读取文件失败: {e}")

    async def _handle_write_file(self, task: dict) -> ToolResult:
        path = task.get("path", "")
        content = task.get("content", "")
        encoding = task.get("encoding", "utf-8")
        try:
            resolved = self._resolve_and_check(path)
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding=encoding)
            size = resolved.stat().st_size
            return ToolResult(
                success=True,
                output=f"成功写入文件: {path} ({size} 字节)",
                data={"path": str(resolved), "size": size},
            )
        except PermissionError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"写入文件失败: {e}")

    async def _handle_append_file(self, task: dict) -> ToolResult:
        path = task.get("path", "")
        content = task.get("content", "")
        encoding = task.get("encoding", "utf-8")
        try:
            resolved = self._resolve_and_check(path)
            resolved.parent.mkdir(parents=True, exist_ok=True)
            with open(resolved, "a", encoding=encoding) as f:
                f.write(content)
            size = resolved.stat().st_size
            return ToolResult(
                success=True,
                output=f"成功追加内容到文件: {path} ({size} 字节)",
                data={"path": str(resolved), "size": size},
            )
        except PermissionError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"追加文件失败: {e}")

    async def _handle_list_directory(self, task: dict) -> ToolResult:
        path = task.get("path", "")
        recursive = task.get("recursive", False)
        pattern = task.get("pattern", "")
        try:
            resolved = self._resolve_and_check(path)
            if not resolved.exists():
                return ToolResult(success=False, error=f"目录不存在: {path}")
            if not resolved.is_dir():
                return ToolResult(success=False, error=f"路径不是目录: {path}")

            items = []
            if recursive:
                for f in resolved.rglob(pattern if pattern else "*"):
                    items.append(f)
            else:
                for f in resolved.glob(pattern if pattern else "*"):
                    items.append(f)

            # 格式化输出
            lines = [f"📁 {path} 的内容 ({len(items)} 项):"]
            for f in sorted(items):
                rel = f.relative_to(resolved)
                if f.is_dir():
                    lines.append(f"  📁 {rel}/")
                else:
                    size = f.stat().st_size
                    mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                    lines.append(f"  📄 {rel}  ({size} bytes, {mtime})")

            return ToolResult(
                success=True,
                output="\n".join(lines),
                data={"items": [str(f) for f in items], "count": len(items)},
            )
        except PermissionError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"列出目录失败: {e}")

    async def _handle_file_info(self, task: dict) -> ToolResult:
        path = task.get("path", "")
        try:
            resolved = self._resolve_and_check(path)
            if not resolved.exists():
                return ToolResult(success=False, error=f"路径不存在: {path}")

            stat = resolved.stat()
            info = {
                "path": str(resolved),
                "type": "directory" if resolved.is_dir() else "file",
                "size": stat.st_size,
                "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "permissions": oct(stat.st_mode),
            }

            output = (
                f"📋 文件信息: {path}\n"
                f"  类型: {info['type']}\n"
                f"  大小: {info['size']} 字节\n"
                f"  创建时间: {info['created']}\n"
                f"  修改时间: {info['modified']}\n"
            )
            return ToolResult(success=True, output=output, data=info)
        except PermissionError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"获取文件信息失败: {e}")

    async def _handle_search_files(self, task: dict) -> ToolResult:
        pattern = task.get("pattern", "")
        root_dir = task.get("root_dir", "")
        max_results = task.get("max_results", 50)
        try:
            root = self._resolve_and_check(root_dir) if root_dir else Path.home()
            if not root.exists():
                return ToolResult(success=False, error=f"目录不存在: {root_dir or '~'}")

            results = []
            for f in root.rglob(pattern):
                if len(results) >= max_results:
                    break
                results.append(f)

            lines = [f"🔍 搜索结果: '{pattern}' 在 {root} ({len(results)} 项)"]
            for f in sorted(results)[:max_results]:
                try:
                    rel = f.relative_to(root)
                    lines.append(f"  {rel}")
                except ValueError:
                    lines.append(f"  {f}")

            if len(results) >= max_results:
                lines.append(f"  ... 还有更多 (仅显示前 {max_results} 项)")

            return ToolResult(
                success=True,
                output="\n".join(lines),
                data={"results": [str(f) for f in results], "count": len(results)},
            )
        except PermissionError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"搜索文件失败: {e}")

    async def _handle_create_directory(self, task: dict) -> ToolResult:
        path = task.get("path", "")
        try:
            resolved = self._resolve_and_check(path)
            resolved.mkdir(parents=True, exist_ok=True)
            return ToolResult(
                success=True,
                output=f"成功创建目录: {path}",
                data={"path": str(resolved)},
            )
        except PermissionError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"创建目录失败: {e}")

    async def _handle_delete(self, task: dict) -> ToolResult:
        path = task.get("path", "")
        recursive = task.get("recursive", False)
        try:
            resolved = self._resolve_and_check(path)
            if not resolved.exists():
                return ToolResult(success=False, error=f"路径不存在: {path}")
            if resolved.is_file():
                resolved.unlink()
                return ToolResult(
                    success=True,
                    output=f"已删除文件: {path}",
                    data={"path": str(resolved), "deleted": True},
                )
            elif resolved.is_dir():
                if recursive:
                    shutil.rmtree(resolved)
                else:
                    resolved.rmdir()
                return ToolResult(
                    success=True,
                    output=f"已删除目录: {path}",
                    data={"path": str(resolved), "deleted": True},
                )
        except PermissionError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"删除失败: {e}")

    async def _handle_execute_command(self, task: dict) -> ToolResult:
        cmd = task.get("command", "")
        timeout = task.get("timeout", 30)
        work_dir = task.get("work_dir", "")

        # 安全检查 —— 只允许读取类和安全命令
        blocked_keywords = [
            "rm -rf /", "rm -rf ~", "mkfs", "dd if=", "> /dev/",
            "shutdown", "reboot", "init 0", "poweroff",
            "chmod 777", "chown", "sudo", "su ",
            ":(){ :|:& };:", "fork bomb",
        ]
        cmd_lower = cmd.lower()
        for blocked in blocked_keywords:
            if blocked in cmd_lower:
                return ToolResult(
                    success=False,
                    error=f"命令包含禁止操作，已拦截: {blocked}",
                )

        try:
            import asyncio
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir if work_dir else None,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                return ToolResult(success=False, error=f"命令执行超时 ({timeout}秒)")

            output = stdout.decode("utf-8", errors="replace")
            error = stderr.decode("utf-8", errors="replace")

            if proc.returncode != 0:
                return ToolResult(
                    success=False,
                    output=output,
                    error=f"命令退出码 {proc.returncode}: {error[:200]}",
                )

            return ToolResult(
                success=True,
                output=output if output else "(命令执行成功，无输出)",
                data={"exit_code": proc.returncode, "stdout": output, "stderr": error},
            )
        except PermissionError as e:
            return ToolResult(success=False, error=str(e))
        except FileNotFoundError as e:
            return ToolResult(success=False, error=f"命令未找到: {e}")
        except Exception as e:
            return ToolResult(success=False, error=f"命令执行失败: {e}")


# ─── 工具注册表 ────────────────────────────────────────────────────

class ToolRegistry:
    """管理所有可用工具的注册与执行。"""

    def __init__(self, fs_tools: Optional[FileSystemTools] = None):
        self._tools: dict[str, ToolDefinition] = {}
        self.fs = fs_tools or FileSystemTools()
        self._register_defaults()

    def _register_defaults(self):
        """注册默认的文件系统工具。"""
        for tool_def in FileSystemTools.get_tool_definitions():
            self.register(tool_def)

    def register(self, tool: ToolDefinition):
        """注册一个工具。"""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def to_llm_tools(self) -> list[dict]:
        """返回 LLM function-calling 格式的工具列表。"""
        return [t.to_llm_format() for t in self._tools.values()]

    async def execute(self, tool_name: str, args: dict) -> ToolResult:
        """执行指定工具。"""
        tool = self._tools.get(tool_name)
        if not tool:
            return ToolResult(success=False, error=f"未知工具: {tool_name}")
        if tool.handler is None:
            return ToolResult(success=False, error=f"工具 {tool_name} 未绑定处理函数")

        # handler 是 FileSystemTools 的实例方法
        try:
            return await tool.handler(self.fs, args)
        except Exception as e:
            return ToolResult(success=False, error=f"工具执行异常: {e}")
