"""Orchestra —— 多智能体协同控制台

使用方式:
    python main.py              # 启动服务（默认 127.0.0.1:8000，开发模式）
    python desktop_entry.py     # 启动桌面模式（打开浏览器，非 reload）
"""

import uvicorn
from app.config import OrchestraSettings
from app.server import create_app

app = create_app()


def main():
    settings = OrchestraSettings()
    print("╔══════════════════════════════════════════════╗")
    print("║         Orchestra 多智能体协同控制台          ║")
    print("╠══════════════════════════════════════════════╣")
    print(f"║  开发模式 → http://{settings.host}:{settings.port}")
    print("║  热加载已启用 · 修改代码自动重启              ║")
    print("║  生产部署用: python desktop_entry.py          ║")
    print("╚══════════════════════════════════════════════╝")
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        reload=True,
    )


if __name__ == "__main__":
    main()
