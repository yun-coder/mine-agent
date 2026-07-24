# Orchestra - 多智能体协同控制台

多 Agent 协同工作工具。页面端可看到所有数据流转，多个类似 cmd 的命令窗口。每个窗口可单独设置角色、具体任务和模型。所有 Agent 由 Master Orchestrator 统一管理。

## 默认 Agent 阵容

| # | Agent | 角色 | 模型 | 模式 |
|---|-------|------|------|------|
| 01 | 🕵️ 情报侦察 | 技术情报 / 信息侦察 | DeepSeek V3.2 | Parallel research |
| 02 | 🏗️ 系统架构 | 架构设计 / 技术选型 | GPT-5 | Sequential handoff |
| 03 | ⚙️ 后端工程 | 后端工程 / 数据建模 | Claude Sonnet 4 | Sequential handoff |
| 04 | 🎨 前端工作台 | 前端工程 / 体验设计 | GPT-5 | Sequential handoff |
| 05 | 🔗 前后端联调 | 前后端联调 / 交付检查 | DeepSeek V3.2 | Sequential handoff |
| 06 | ✅ 回归测试 | 测试 / 复盘学习 | Qwen 3 235B | Review only |

## 快速开始

### 开发模式（热加载）

```bash
uv sync
python main.py
# 打开 http://127.0.0.1:8000
```

### 桌面 exe 模式

```bash
python build_exe.py
dist\Orchestra\Orchestra.exe
# 或双击 dist\启动控制台.bat
```

> 不配置 LLM API Key 时系统使用模拟回复，UI 功能完整可交互。

## 主机文件访问

每个 Agent 都内置文件系统工具，可读写主机文件、执行命令：

| 工具 | 功能 |
|------|------|
| `read_file` | 读取文件内容 |
| `write_file` | 写入/创建文件 |
| `append_file` | 追加内容到文件 |
| `list_directory` | 列出目录内容 |
| `search_files` | 按 glob 模式搜索文件 |
| `file_info` | 获取文件元信息 |
| `create_directory` | 创建目录 |
| `delete_file` | 删除文件或目录 |
| `execute_command` | 执行 shell 命令（安全过滤） |

所有操作受安全检查约束，防止路径穿越和危险命令。

Agent 在推理过程中自动调用工具，调用过程和结果实时展示在前端终端。

## 打包为桌面 exe

```bash
python build_exe.py
```

输出 `dist/Orchestra/Orchestra.exe`（约 11MB），双击即可运行，自动打开浏览器。

打包命令说明：

| 参数 | 说明 |
|------|------|
| `--onedir` | 文件夹模式，比单文件更稳定 |
| `--console` | 保留控制台窗口显示日志 |
| `--add-data` | 打包 `public/` 前端静态文件 |
| `--hidden-import` | 显式声明所有动态导入模块 |

## LLM 配置

在 `.env` 中配置 API Key（参考 `.env.example`）：

```ini
# OpenAI / 兼容 API
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# DeepSeek
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

## 系统架构

```
┌──────────────────────────────────────────────────────────┐
│                    Frontend (SPA)                         │
│          DeepSeek 风格命令行终端 + 审计面板                │
│          支持流式渲染 / 工具调用可视化 / WebSocket          │
└──────────────────────┬───────────────────────────────────┘
                       │ WebSocket / REST
┌──────────────────────▼───────────────────────────────────┐
│              Master Orchestrator                          │
│         流水线编排 / 状态广播 / 命令路由 / 事件审计         │
├──────────────────────────────────────────────────────────┤
│   Agent Registry    │    Pipeline Engine                  │
│   (6 默认 + N 自定义) │   (顺序/并行/审查三种模式)          │
├──────────────────────────────────────────────────────────┤
│    LLM Providers     │    Tool System                     │
│  OpenAI/Anthropic/   │    文件读写 / 目录操作 / 命令执行    │
│  DeepSeek/Mock       │    (安全检查 + 路径沙箱)            │
├──────────────────────────────────────────────────────────┤
│    Context Memory    │    Handoff Audit                   │
│    对话历史管理        │    交接日志 / Token 统计            │
└──────────────────────────────────────────────────────────┘
```

## 流水线模式

- **Sequential handoff**: Agent 顺序执行，前一个完成后自动交接给下一个
- **Parallel research**: 多个 Agent 并发研究，结果汇总后喂给后续 Sequential 组
- **Review only**: 审查模式，在流水线末尾作为质量关卡

执行顺序：`Parallel research → Sequential handoff → Review only`

## 项目结构

```
first-agents/
├── app/
│   ├── agents/            # Agent 系统（6 个角色 + 基类 + 注册表）
│   │   ├── base.py        #   抽象基类（工具调用循环）
│   │   ├── scout.py       #   01 情报侦察 Agent
│   │   ├── architect.py   #   02 系统架构 Agent
│   │   ├── backend_dev.py #   03 后端工程 Agent
│   │   ├── frontend_dev.py#   04 前端工作台 Agent
│   │   ├── bridge.py      #   05 前后端联调 Agent
│   │   ├── tester.py      #   06 回归测试 Agent
│   │   └── registry.py    #   Agent 注册表
│   ├── llm/               # LLM 提供商层
│   │   ├── base.py        #   抽象接口
│   │   ├── registry.py    #   工厂 + MockProvider
│   │   └── providers/     #   OpenAI/Anthropic/Generic
│   ├── tools/             # 主机文件系统工具
│   │   ├── __init__.py    #   9 个文件/命令工具
│   │   └── engine.py      #   工具调用引擎
│   ├── memory/            # 记忆与审计
│   │   ├── context.py     #   对话上下文管理
│   │   └── store.py       #   交接日志
│   ├── pipeline/engine.py # 流水线编排引擎
│   ├── web/routes.py      # REST + WebSocket 端点
│   ├── ws_manager.py      # WebSocket 连接管理
│   ├── orchestrator.py    # Master 主控编排器
│   ├── models.py          # 全部 Pydantic 数据模型
│   ├── config.py          # 配置（Pydantic Settings）
│   └── server.py          # FastAPI 应用工厂
├── public/index.html      # 前端 SPA
├── tests/                 # 集成测试（16 个用例）
├── main.py                # 开发入口
├── desktop_entry.py       # 桌面 exe 入口
├── build_exe.py           # PyInstaller 打包脚本
├── pyproject.toml         # 项目配置
└── .env.example           # 环境变量模板
```

## 运行测试

```bash
pytest tests/ -v
# 预期: 15 passed, 1 skipped
```

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.13+ / FastAPI / WebSocket / Pydantic |
| 前端 | 纯 HTML/CSS/JS（SPA，暗色 DeepSeek 风格） |
| LLM | OpenAI / Anthropic / DeepSeek 兼容 API |
| 工具系统 | 异步文件操作 + 命令执行 + 路径安全沙箱 |
| 桌面打包 | PyInstaller（11MB onedir） |
| 测试 | pytest / pytest-asyncio / httpx / websockets |
