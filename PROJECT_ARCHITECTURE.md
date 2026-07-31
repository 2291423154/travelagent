# TravelAgent — 项目架构说明

> 基于 LangGraph ReAct Agent + RAG 知识库 + 高德 MCP 实时数据的智能旅行助手。

---

## 一、整体架构

```
┌──────────────────────────────────────────────┐
│                  前端层                        │
│  CLI (Rich TUI)  │  Web SPA  │  飞书 Bot      │
└──────────────────┬───────────────────────────┘
                   │ HTTP REST
┌──────────────────▼───────────────────────────┐
│               FastAPI 后端                     │
│  路由: /agent/invoke  /agent/resume  /agent/status │
└──────────────────┬───────────────────────────┘
                   │ Redis (Celery Broker)
┌──────────────────▼───────────────────────────┐
│            Celery Worker (异步)                │
│  invoke_agent_task  │  resume_agent_task      │
│       │              │         │              │
│       ▼              ▼         ▼              │
│  ┌──────┐  ┌──────────┐  ┌──────────┐        │
│  │ ReAct │  │  LangGraph │  │  MCP/Tool │      │
│  │ Agent │  │ Checkpoint │  │ Executor  │      │
│  └──────┘  └──────────┘  └──────────┘        │
└──────────────────────────────────────────────┘
```

**为什么用 Celery 异步任务？** Agent 一次推理可能 10-30 秒，如果 Backend 同步等结果，HTTP 请求会超时。Celery 让 Backend 立即返回 task_id，Worker 在后台慢慢跑，前端轮询拿结果。

---

## 二、三层存储架构

```
┌─────────────────────────────────┐
│            Redis                 │
│  快，内存，自动过期              │
│  ┌───────────────────────────┐  │
│  │ Celery 消息 Broker         │  │ ← 任务队列：Backend 扔任务，
│  │                           │  │    Worker 取任务
│  │ 会话状态                   │  │ ← idle / running / interrupted
│  │ (TTL 300s 自动清理)       │  │    用户切出 5 分钟后自动过期
│  │                           │  │
│  │ 任务元数据                 │  │ ← 临时中间结果，毫秒级查询
│  └───────────────────────────┘  │
├─────────────────────────────────┤
│          PostgreSQL              │
│  结构化，持久，事务              │
│  ┌───────────────────────────┐  │
│  │ LangGraph Checkpoint       │  │ ← Agent 每一步的状态快照
│  │ (AsyncPostgresSaver)       │  │    Thought/Action/Observation
│  │                           │  │    HITL 中断后可断点续跑
│  │                           │  │
│  │ LangGraph Store             │  │ ← 长期记忆（用户偏好、习惯）
│  │ (AsyncPostgresStore)       │  │    跨会话持久化
│  └───────────────────────────┘  │
├─────────────────────────────────┤
│            Chroma                │
│  向量，语义检索                  │
│  ┌───────────────────────────┐  │
│  │ 旅游攻略知识库              │  │ ← 9 个 PDF，147 chunks
│  │ (持久化到本地磁盘)          │  │    语义检索 + LLM 生成
│  └───────────────────────────┘  │
└─────────────────────────────────┘
```

### 为什么三层分开，不全放 Redis？

| 数据 | 存哪里 | 原因 |
|------|--------|------|
| 会话临时状态 | Redis | 毫秒级读写，TTL 自动清理，不用手动删过期数据 |
| Agent 状态快照 | PostgreSQL | 需要事务保证和持久化——断电不丢 |
| 用户偏好/习惯 | PostgreSQL | 结构化查询——"查这个用户的所有偏好"用 SQL 比 Redis Hash 方便 |
| 知识库向量 | Chroma | 专用向量索引（HNSW），语义检索不是 Redis 的设计目标 |

### 为什么用 PostgreSQL 而不是 SQLite？

Agent 的 HITL 中断需要**并发读写**——Celery Worker 在写 Agent 的状态快照，同时 Backend 在读状态返回给前端，前端的恢复请求也在写。SQLite 是单写锁，并发场景会卡住。PostgreSQL 支持真正的行级并发。

---

## 三、基础设施（Docker）

```
docker-compose.yml
  ├── postgres:15     → localhost:5432
  └── redis:latest    → localhost:6379
```

**为什么用 Docker 而不是本地安装？**

- 一键启动所有依赖：`docker-compose up -d`
- 环境一致：不会出现"我电脑上的 PostgreSQL 是 14，你的是 16"的问题
- 不污染系统：项目删了容器就没了，不需要卸载重装
- 面试官如果想复现，拉代码后一条命令就能跑

---

## 四、RAG 模块

```
离线阶段（建库，只跑一次）
  文档加载 → PDF 解析 → 切分(500字+100字overlap) → Embedding → Chroma
         ↑
  支持格式: .txt / .pdf（PyPDF2 提取文本）
  Embedding: qwen3.7-text-embedding（阿里百炼，1024维）
  索引方式: HNSW（余弦相似度）
  文档: 9 个江苏旅游攻略 PDF → 147 chunks

在线阶段（每次查询）
  Query → Embedding → Chroma 语义检索 Top 3 → 格式化 → 注入 Agent Context
                                         ↓
                                      search_travel_knowledge 工具
```

**和 MCP 工具的区别**：RAG 查的是**静态知识**（历史典故、游玩攻略、美食推荐），MCP 工具查的是**实时数据**（天气、交通、周边搜索）。Agent 根据 Query 自动判断该调哪个。

---

## 五、Agent 工具链

| 工具 | 来源 | 用途 |
|------|------|------|
| maps_text_search | 高德 MCP | 搜索 POI（餐厅、景点） |
| maps_geo | 高德 MCP | 地址 → 经纬度 |
| maps_distance | 高德 MCP | 计算两点距离 |
| maps_direction_transit | 高德 MCP | 公交/地铁路线规划 |
| maps_weather | 高德 MCP | 实时天气查询 |
| search_travel_knowledge | 本地 RAG | 旅游攻略知识库 |
| book_hotel | 自定义 | 模拟酒店预订 |
| multiply | 自定义 | 计算器 |

所有工具调用前通过 HITL（Human-in-the-Loop）审核——用户在终端输入 yes/no/edit/response 控制工具是否执行。

---
