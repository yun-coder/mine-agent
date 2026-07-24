# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['D:\\学习院\\first-agents\\desktop_entry.py'],
    pathex=[],
    binaries=[],
    datas=[('D:\\学习院\\first-agents\\public', 'public')],
    hiddenimports=['uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto', 'uvicorn.middleware.asgi2', 'uvicorn.middleware.wsgi', 'fastapi', 'pydantic', 'pydantic_settings', 'websockets', 'httpx', 'anyio', 'sniffio', 'openai', 'starlette', 'starlette.routing', 'starlette.middleware', 'starlette.staticfiles', 'starlette.websockets', 'app', 'app.agents', 'app.agents.base', 'app.agents.scout', 'app.agents.architect', 'app.agents.backend_dev', 'app.agents.frontend_dev', 'app.agents.bridge', 'app.agents.tester', 'app.agents.registry', 'app.llm', 'app.llm.base', 'app.llm.registry', 'app.llm.providers', 'app.llm.providers.openai_provider', 'app.llm.providers.anthropic_provider', 'app.llm.providers.generic_provider', 'app.memory', 'app.memory.context', 'app.memory.store', 'app.pipeline', 'app.pipeline.engine', 'app.web', 'app.web.routes', 'app.tools', 'app.tools.engine', 'app.ws_manager', 'app.orchestrator', 'app.models', 'app.config', 'app.server'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Orchestra',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Orchestra',
)
