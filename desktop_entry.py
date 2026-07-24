"""Orchestra 桌面入口 —— 启动服务并打开浏览器。

此文件是 PyInstaller 打包的主入口。
"""

import webbrowser
import threading
import time
import sys
import os

# 将项目根目录添加到路径（打包后 __file__ 在解压目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
from app.config import OrchestraSettings
from app.server import create_app

# 创建应用实例（供 uvicorn 直接使用，避免 factory 模式在打包后失效）
app = create_app()


def open_browser(host: str, port: int, delay: float = 1.5):
    """延迟打开浏览器。"""
    def _open():
        time.sleep(delay)
        webbrowser.open(f"http://{host}:{port}")
    threading.Thread(target=_open, daemon=True).start()


def main():
    settings = OrchestraSettings()
    host = settings.host
    port = settings.port

    print("==================================================")
    print("  Orchestra - Multi-Agent Orchestration Console")
    print("==================================================")
    print(f"  URL: http://{host}:{port}")
    print("  Agents: 情报 | 架构 | 后端 | 前端 | 联调 | 测试")
    print("  File System: Agents can access host files")
    print("  Close terminal to exit")
    print("==================================================")
    print()

    # 打开浏览器
    open_browser(host, port)

    # 启动 uvicorn（直接传 app 实例，避免 factory 模式在 PyInstaller 打包后失效）
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=settings.log_level,
        reload=False,
    )


if __name__ == "__main__":
    main()
