# 开发日志 (DevLog)

> 项目：ReActAgentHILApiMultiSessionTask — 生产级 Agent 智能体服务
> 遵循：每次变更必须记录，确保可追溯性

---

## 2026-07-06 — 飞书机器人集成 ✅

### 新增文件
| 文件 | 行数 | 说明 |
|---|---|---|
| `utils/feishu.py` | ~200 | 飞书 SDK 封装（WSClient + 消息收发 + 重连） |
| `03_feishuBot.py` | ~220 | 飞书 Bot 主进程（消息→Agent→回复） |

### 修改文件
| 文件 | 变更 |
|---|---|
| `utils/config.py` | 新增 `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_AUTO_ACCEPT_TOOLS` |
| `02_frontendServer.py` | 从 05 项目复制，适配 task_id 参数，新增 task_id 全局追踪 |

### 技术选型
- SDK: `lark-oapi`（飞书官方 Python SDK）
- 连接: WebSocket（无需公网 URL）
- 事件: `im.message.receive_v1`
- 消息: `im.v1.message.create/reply`
- HITL: 默认自动批准工具调用，可配置为手动模式

### 启动方式
```bash
$env:FEISHU_APP_ID="cli_xxx"
$env:FEISHU_APP_SECRET="xxx"
python 03_feishuBot.py          # 飞书 Bot
python 02_frontendServer.py     # Rich CLI 前端
```

---

## 2026-07-06 — 环境搭建与后端服务验证通过 ✅

### Conda 环境
- 环境名：`ReActAgents`，Python 3.11，位置 `D:\envs\ReActAgents`
- 激活：`source /c/Users/lenovo/miniconda3/etc/profile.d/conda.sh && conda activate ReActAgents`

### 依赖安装（版本冻结）
| 包 | 版本 | 说明 |
|---|---|---|
| langgraph | 0.4.5 | ReAct Agent 框架 |
| langchain | 0.3.25 | LLM 编排框架 |
| langchain-core | 0.3.86 | 核心类型（锁定版本） |
| langchain-openai | 0.3.17 | OpenAI 兼容接口 |
| langgraph-checkpoint-postgres | 2.0.21 | PostgreSQL checkpointer |
| langgraph-prebuilt | 0.1.8 | 预置 Agent（兼容 langgraph 0.4.5） |
| langchain-mcp-adapters | 0.1.14 | MCP 客户端适配（锁定版本） |
| fastapi | 0.139.0 | **升级**以兼容 starlette 1.3.1 |
| starlette | 1.3.1 | 由 MCP 依赖升级 |
| psycopg | 3.3.4 + psycopg-binary 3.3.4 | 异步 PostgreSQL |
| celery | 5.5.3 | 异步任务队列 |
| redis | 6.2.0 | Redis 客户端 |

### 版本冲突解决记录
1. `langgraph-prebuilt 1.0.1` 与 `langgraph 0.4.5` 不兼容 → 降级到 `0.1.8`
2. 缺少 `langchain-mcp-adapters` → 安装 `0.1.14`（兼容 langchain-core 0.3.x）
3. `langchain-mcp-adapters 0.3.0` 要求 `langchain-core>=1.0.0` → 回退到 `0.1.14`
4. `starlette 1.3.1` 与 `fastapi 0.115.12` 不兼容 → 升级 FastAPI 到 `0.139.0`
5. 缺少 `psycopg-binary` 扩展 → 安装 `3.3.4`

### 基础设施
- PostgreSQL 15：Docker 启动，`localhost:5432`，用户 `kevin`，密码 `123456`
- Redis：Docker 启动，`localhost:6379`
- LLM：阿里通义千问（`DASHSCOPE_API_KEY` 已配置）

### 代码修复
- `01_backendServer.py:6-7`：Windows 事件循环策略移到文件顶部，避免 ProactorEventLoop 错误

## 2026-07-06 — LLM 切换：阿里千问 → 科大 API ✅

### 变更内容
- **LLM 后端**：从阿里 DashScope 切换到科大 API（`api.llm.ustc.edu.cn`）
- **模型**：`qwen-chat`（支持 Function Calling）
- **配置修改**：
  - `utils/config.py:29`：`LLM_TYPE = "openai"`
  - `utils/llms.py:33-38`：openai 配置改为科大 API 的 base_url 和 chat_model
- **SSL 修复**：conda 环境的 `SSL_CERT_FILE` 缺失，从 miniconda3 复制 cacert.pem
- **验证结果**：LLM 连接成功、17 个工具全部加载（15 个高德地图 + book_hotel + multiply）

### API 验证
- `GET /system/info` → `200 OK`，`{"sessions_count":0,"active_users":{}}`
- 下一步：启动 Celery Worker 进行完整 Agent 功能测试

---

## 2026-07-06 — 项目初始化与 Git 仓库建立

### 变更内容
- **Git 初始化**：`git init` 创建本地 Git 仓库
- **.gitignore**：创建忽略规则，排除 `__pycache__/`、`logfile/`、`.env`、PDF 文档、IDE 配置等
- **devlog.md**：创建本开发日志文件，建立变更记录规范

### 当前项目状态
| 文件 | 状态 | 说明 |
|---|---|---|
| `01_backendServer.py` | 已完成 | FastAPI 后端，11 个 API 端点 |
| `02_frontendServer.py` | **空文件** | Rich 前端未实现 |
| `utils/config.py` | 已完成 | 配置中心，默认使用 qwen |
| `utils/llms.py` | 已完成 | 4 种 LLM 方案适配 |
| `utils/tools.py` | 已完成 | HITL 工具 + MCP Server |
| `utils/tasks.py` | 已完成 | Celery 异步任务 + ReAct Agent |
| `utils/redis.py` | 已完成 | Redis 会话管理器 |
| `utils/models.py` | 已完成 | Pydantic 数据模型 |
| `redisTest.py` | 已完成 | Redis 会话管理器独立测试 |
| `docker/postgresql/` | 已完成 | PostgreSQL 15 Docker Compose |
| `docker/redis/` | 已完成 | Redis Docker Compose |

### 下一步计划
1. 创建 conda 虚拟环境 `ReActAgents`（Python 3.11）
2. 安装项目依赖
3. 配置 LLM API 密钥
4. 启动 Docker 基础设施（PostgreSQL + Redis）
5. 启动 Celery Worker + 后端 API 服务
6. 验证 API 端点

### 提交记录
- `30b9e65` — 初始提交：完整项目代码（15 files, 2852 lines）

### 约定
- **Commit 粒度**：每个可独立运行的变更一次提交
- **Commit 格式**：`<type>: <简短描述>`（feat/fix/refactor/docs/chore）
- **每次提交前**：更新本 devlog 对应日期的变更内容
- **分支策略**：master 为主分支，功能开发切 feature/* 分支

---