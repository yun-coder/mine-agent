"""
Orchesta 打包脚本 —— 使用 PyInstaller 构建桌面 exe。

用法:
    python build_exe.py

输出:
    dist/Orchestra/Orchestra.exe
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    dist_dir = root / "dist"
    build_dir = root / "build"
    spec_file = root / "orchestra.spec"

    # 清理旧构建
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    if build_dir.exists():
        shutil.rmtree(build_dir)

    print("=" * 60)
    print("  Orchestra 桌面版打包")
    print("=" * 60)
    print()

    # 1. 先检测 PyInstaller
    try:
        import PyInstaller
        print(f"  PyInstaller {PyInstaller.__version__} 已就绪")
    except ImportError:
        print("  安装 PyInstaller...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            cwd=root,
        )

    # 2. 构建命令
    entry = root / "desktop_entry.py"

    # 确定 public 数据目录
    public_dir = root / "public"
    separator = ";" if sys.platform == "win32" else ":"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean",
        "--noconfirm",
        "--name", "Orchestra",
        "--onedir",                    # 文件夹模式（比 onefile 更稳定）
        "--console",                   # 保留控制台窗口（显示日志）
        "--add-data", f"{public_dir}{separator}public",
        # 隐藏导入（处理动态导入）
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.middleware.asgi2",
        "--hidden-import", "uvicorn.middleware.wsgi",
        "--hidden-import", "fastapi",
        "--hidden-import", "pydantic",
        "--hidden-import", "pydantic_settings",
        "--hidden-import", "websockets",
        "--hidden-import", "httpx",
        "--hidden-import", "anyio",
        "--hidden-import", "sniffio",
        "--hidden-import", "openai",
        "--hidden-import", "starlette",
        "--hidden-import", "starlette.routing",
        "--hidden-import", "starlette.middleware",
        "--hidden-import", "starlette.staticfiles",
        "--hidden-import", "starlette.websockets",
        "--hidden-import", "app",
        "--hidden-import", "app.agents",
        "--hidden-import", "app.agents.base",
        "--hidden-import", "app.agents.scout",
        "--hidden-import", "app.agents.architect",
        "--hidden-import", "app.agents.backend_dev",
        "--hidden-import", "app.agents.frontend_dev",
        "--hidden-import", "app.agents.bridge",
        "--hidden-import", "app.agents.tester",
        "--hidden-import", "app.agents.registry",
        "--hidden-import", "app.llm",
        "--hidden-import", "app.llm.base",
        "--hidden-import", "app.llm.registry",
        "--hidden-import", "app.llm.providers",
        "--hidden-import", "app.llm.providers.openai_provider",
        "--hidden-import", "app.llm.providers.anthropic_provider",
        "--hidden-import", "app.llm.providers.generic_provider",
        "--hidden-import", "app.memory",
        "--hidden-import", "app.memory.context",
        "--hidden-import", "app.memory.store",
        "--hidden-import", "app.pipeline",
        "--hidden-import", "app.pipeline.engine",
        "--hidden-import", "app.web",
        "--hidden-import", "app.web.routes",
        "--hidden-import", "app.tools",
        "--hidden-import", "app.tools.engine",
        "--hidden-import", "app.ws_manager",
        "--hidden-import", "app.orchestrator",
        "--hidden-import", "app.models",
        "--hidden-import", "app.config",
        "--hidden-import", "app.server",
        str(entry),
    ]

    print("  运行 PyInstaller...")
    print(f"  入口: {entry.name}")
    print(f"  输出: {dist_dir / 'Orchestra'}")
    print()

    result = subprocess.run(cmd, cwd=root, capture_output=False)

    if result.returncode != 0:
        print()
        print("!" * 60)
        print("  打包失败！")
        print("!" * 60)
        sys.exit(1)

    print()
    print("=" * 60)
    print("  ✓ 打包成功！")
    print(f"  输出目录: {dist_dir / 'Orchestra'}")
    print(f"  启动: {dist_dir / 'Orchestra' / 'Orchestra.exe'}")
    print("=" * 60)

    # 3. 复制 .env.example（如果有）
    env_example = root / ".env.example"
    if env_example.exists():
        shutil.copy2(env_example, dist_dir / "Orchestra" / ".env.example")
        print("  ✓ 已复制 .env.example")

    # 4. 创建启动脚本
    bat_path = root / "dist" / "启动控制台.bat"
    exe_rel = "Orchestra\\Orchestra.exe"
    bat_content = f"""@echo off
cd /d "%~dp0"
start "" "{exe_rel}"
exit
"""
    bat_path.write_text(bat_content, encoding="utf-8")
    print(f"  ✓ 已创建启动脚本: {bat_path}")


if __name__ == "__main__":
    main()
