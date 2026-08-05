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
│          向量索引                │
│  numpy，余弦相似度               │
│  ┌───────────────────────────┐  │
│  │ 旅游攻略知识库              │  │ ← 20 份文档，495 chunks
│  │ (JSON + numpy 向量存储)     │  │    语义检索 + LLM 生成
│  └───────────────────────────┘  │
└─────────────────────────────────┘
```

### 为什么三层分开，不全放 Redis？

| 数据 | 存哪里 | 原因 |
|------|--------|------|
| 会话临时状态 | Redis | 毫秒级读写，TTL 自动清理，不用手动删过期数据 |
| Agent 状态快照 | PostgreSQL | 需要事务保证和持久化——断电不丢 |
| 用户偏好/习惯 | PostgreSQL | 结构化查询——"查这个用户的所有偏好"用 SQL 比 Redis Hash 方便 |
| 知识库向量 | numpy 索引 | 余弦相似度检索，轻量零依赖，语义检索不是 Redis 的设计目标 |

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
  文档加载 → 切分(500字+100字overlap) → Embedding → numpy 向量索引
         ↑
  支持格式: .txt（pypdf 可选支持 PDF）
  Embedding: text-embedding-v1（阿里百炼，1536维）
  索引方式: 余弦相似度（numpy）
  文档: 20 份文档（11 TXT + 9 PDF）→ 495 chunks
  切分细节: 按段落边界递归切分；超长段落按句末标点二次切分，
            适配 Embedding 模型 2048 token 输入上限

在线阶段（每次查询）
  Query → Embedding → numpy 余弦相似度检索 Top 3 → 格式化 → 注入 Agent Context
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
| search_hotels | Amadeus API | 搜索真实酒店（含价格、评分、空房） |
| book_hotel | 自定义 | 确认酒店预订（含参数校验和订单号） |
| get_current_time | 自定义 | 获取当前时间 |

所有工具调用前通过 HITL（Human-in-the-Loop）审核——用户在终端输入 yes/no/edit/response 控制工具是否执行。

---

## 六、评估体系与迭代结果

### 评估设计

自建 20 条自动化评估脚本（`tests/eval_agent.py`），覆盖四类场景：

| 场景 | 测什么 | 示例 |
|------|--------|------|
| 纯 RAG | 知识库检索质量 | "南京有什么特色小吃推荐？" |
| 纯 MCP | 实时 API 工具调用 | "帮我查南京新街口附近有什么商场" |
| 混合 RAG+MCP | 多工具协同 | "推荐南京好吃的餐厅，并告诉我从南京站怎么过去" |
| 边缘 Case | 鲁棒性 | "火星上有什么好吃的餐厅？" |

### 迭代结果

| 轮次 | 模型 | 完成率 | 纯RAG | 纯MCP | 混合 | 边缘 |
|------|------|--------|-------|-------|------|------|
| v1 | Qwen-3.7 | 25% | 2/5 | 0/5 | 0/5 | 3/5 |
| v2 | GLM-5.2 | 45% | 4/5 | 1/5 | 0/5 | 4/5 |
| **v3** | **deepseek-v4-flash** | **80%** | **5/5** | **5/5** | **1/5** | **5/5** |

**v1 → v3 的关键优化**：
1. **工具描述重构**：RAG 工具标注"【首选】查攻略/美食/历史文化——高德只返回 POI 坐标"，修复 Agent 错误走高德查攻略的问题
2. **recursion_limit 硬限制**：从代码层限制 ReAct 步数，防止无限循环
3. **System Prompt 路由规则**：明确"攻略→RAG，实时→高德，混合→先 RAG 再 MCP"
4. **模型选型**：最终采用 deepseek-v4-flash（工具路由准确 + Prompt Caching 命中率高）

### 混合任务优化（v4）

**问题**：混合 RAG+MCP 任务（v3 仅 1/5 通过）——复合 Query 需 6+ 步串行工具调用，易超 recursion_limit。典型链路：search_travel_knowledge → maps_text_search → maps_search_detail → maps_geo → maps_direction_transit → maps_direction_driving。

**方案**：新增 Workflow 层——Agent 启动前用分类模型判断查询复杂度，混合查询动态提高 recursion_limit（8→10）并收紧工具体使用约束（限制推荐逐家对比、要求只给最优路线）。

**效果**：混合任务完成率从 1/5 提升至 4/5，总体评估提升至 95%。剩余 1 条（南京美食+交通）因 Agent 搜到餐厅后逐家查路线导致步数暴走，后续可进一步优化 System Prompt 约束。

---

## 七、Langfuse 可观测性

### 为什么选 Langfuse 而非 LangSmith

| | LangSmith | Langfuse |
|---|-----------|---------|
| 集成 LangGraph | 环境变量 3 行 | 需要 callback handler |
| 免费额度 | 个人版 5000 trace/月 | 无限制 |
| 数据 | 云端托管 | 可自托管 |
| 适合场景 | 小体量快速接入 | 评估量大、数据可控 |

项目用 LangGraph，默认方案是 LangSmith，但评估体系一轮跑 20 条 Query 会产生大量 Trace，LangSmith 个人版额度扛不住，故选 Langfuse。

### 集成要点

- `get_langfuse_handler()` 无参构造（langfuse v3 API），每次 Agent 调用创建 handler
- **async 场景必须手动 flush**：Agent 在 Celery 异步任务中执行，`agent.ainvoke` 后立即 `handler.flush()`，否则 Trace 不落盘
- 每次执行生成完整 Trace：LLM 调用（System Prompt/用户问题/回复）、工具调用（各自输入输出）、Token 消耗、耗时

### 价值

替代纯日志排查——打开 Langfuse 面板即可直观看到 Agent 每一步选了哪个工具、哪次调用 Token 超支、哪次工具超时。

---
