# TravelAgent — AI 智能旅行助手

> 基于 LangGraph ReAct Agent + RAG 知识库 + 高德 MCP 实时数据的旅游 Agent 系统。
> **我独立完成的部分**：① 旅游攻略 RAG 知识库（PDF 解析 + Chroma + 语义检索） ② 20 条自动化评估脚本 + 根因驱动的迭代优化 ③ 多模型适配（Qwen-3.7 / GLM-5.2）+ 多 Key 安全管理 ④ 工具描述优化 + System Prompt 路由规则 + recursion_limit 硬限制 ⑤ 项目架构文档 + 面试准备材料

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

## 六、面试话术速查

**"讲一下你的项目架构"**

> 这是一个生产级的 Agent 服务，三层架构。前端支持 CLI、Web 和飞书 Bot。FastAPI 后端接收请求后通过 Celery 异步任务队列分发到 Worker，Worker 里跑 LangGraph 的 ReAct Agent。存储用了三层——Redis 放会话状态和消息队列（快，自动过期），PostgreSQL 存 Agent 状态快照和长期记忆（事务保证，并发安全），Chroma 存旅游攻略知识库向量（语义检索）。基础设施用 Docker Compose 一键启动。

**"为什么用三层存储而不是一个数据库全搞定？"**

> 每种存储有它的最优场景。Redis 是内存数据库，毫秒级读写，会话状态这种临时的、有 TTL 的数据放 Redis 最合适——用户 5 分钟不操作自动清理，不需要写定时任务。PostgreSQL 有事务保证和行级锁，Agent 的状态快照和长期记忆需要持久化且支持并发读写，这是 SQLite 做不了的。Chroma 是专用向量数据库，构建 HNSW 索引做语义检索，这不是 PostgreSQL 的设计目标。

**"HITL 怎么实现的？"**

> LangGraph 的 interrupt 机制。每个工具调用前挂起 Agent，把当前状态存到 PostgreSQL Checkpoint，通知用户审批。审批通过后从断点恢复继续执行。四种审批模式——accept 批准、reject 拒绝、edit 修改参数、response 不调工具直接反馈。


---

## 七、优化迭代记录（面试时讲我遇到了什么问题，怎么解决的）

### 优化 1：RAG 工具描述强化

**问题**：GLM 模型倾向于走高德 maps_text_search 查美食/攻略，忽略了 RAG 工具。高德返回的是 POI 坐标列表，没有深度攻略和历史文化。

**根因**：RAG 工具描述太弱——没说明什么时候用它、它和高德有什么区别。

**修复**：强化描述为"【首选工具】当用户问'XX有什么好吃的/好玩的/值得去的/历史典故'时优先使用。不要用高德代替——高德只返回 POI 坐标，没有深度攻略和人文内容。此工具和高德互补：此工具拿知识，高德拿实时数据。"

### 优化 2：recursion_limit 硬限制

**问题**：System Prompt 写"最多调 3 次工具"但 Agent 不理——ReAct 模式下 Agent 会持续深挖。

**修复**：在 create_react_agent 的 config 中设 recursion_limit: 8，从 LangGraph 代码层硬限制 Loop 步数。Prompt 约束（最多 5 次）+ 代码硬限制双层兜底。

### 优化 3：HITL 批量确认（评估加速）

**问题**：每条工具调用都要等轮询间隔，20 条 Query 耗时 > 20 分钟。

**思路**：简单的查询类工具（非删除/非修改）可以批量自动审批，不逐个弹窗。评估脚本轮询从 3s 降到 1s。

### 优化 4：System Prompt 精准路由

**问题**：没有告诉 Agent 什么场景用什么工具。

**修复**：新增工具路由规则——"攻略/美食/历史→search_travel_knowledge，实时天气/交通/导航→高德 maps_系列，混合问题→先 RAG 再 MCP"。

---

## 八、面试话术速查

**"讲一下你的项目架构"**

> 这是一个生产级的 Agent 服务，三层架构。前端支持 CLI、Web 和飞书 Bot。FastAPI 后端接收请求后通过 Celery 异步任务队列分发到 Worker，Worker 里跑 LangGraph 的 ReAct Agent。存储用了三层——Redis 放会话状态和消息队列（快，自动过期），PostgreSQL 存 Agent 状态快照和长期记忆（事务保证，并发安全），Chroma 存旅游攻略知识库向量（语义检索）。基础设施用 Docker Compose 一键启动。

**"为什么用三层存储而不是一个数据库全搞定？"**

> 每种存储有它的最优场景。Redis 是内存数据库，毫秒级读写，会话状态这种临时的、有 TTL 的数据放 Redis 最合适——用户 5 分钟不操作自动清理。PostgreSQL 有事务保证和行级锁，Agent 的状态快照和长期记忆需要持久化且支持并发读写。Chroma 是专用向量数据库，构建 HNSW 索引做语义检索，这不是 PostgreSQL 的设计目标。

**"HITL 怎么实现的？"**

> LangGraph 的 interrupt 机制。每个工具调用前挂起 Agent，把当前状态存到 PostgreSQL Checkpoint，通知用户审批。审批通过后从断点恢复继续执行。四种审批模式——accept 批准、reject 拒绝、edit 修改参数、response 不调工具直接反馈。

**"你做了什么优化？"**

> 我做了四轮优化。第一轮分析评估失败根因——发现 Agent 不走 RAG 工具而是错误地走高德查攻略，根因是 RAG 工具描述没有说明它和高德的区别。修复后 RAG 类完成率从 2/5 提升到 4/5。第二轮加了 recursion_limit 代码层硬限制解决死循环。第三轮优化了 System Prompt 加入工具路由规则。混合任务仍然是我下一步重点——需要 Workflow 层识别复合 Query 后拆成子任务并行执行。


## 八、评估体系与迭代优化（STAR 格式）

### S — Situation（业务背景）

Agent 开发中最容易被忽略的问题是"它到底好不好"——没有评估体系的 Agent 是盲飞。我给 ReActAgent 设计了系统化评估：20 条测试 Query 覆盖四类场景，量化 Agent 在不同维度下的表现。

### T — Task（我的目标）

建立可复现的评估基线，定位 Agent 在哪类任务上表现差、根因是什么、如何优化。目标是把完成率从初始基线提升到 70%+。

### A — Action（我做了什么、为什么这么做）

**第一轮（Qwen-3.7，完成率 25%）**

跑完 20 条后逐条分析 Celery 日志中的 Thought-Action-Observation 链路，定位了三个根因：

| 问题 | 根因 | 证据 |
|------|------|------|
| 纯 MCP 全部失败 | HITL 轮询吃时间 + 高德公交/天气 API 超时 | 每条工具等 3s × 调 7+ 次 = 光等待就 40s+ |
| 混合任务全部超限 | Agent 没遵守"最多 3 次工具"的 Prompt 约束 | 日志 tools=13，超过 recursion_limit |
| 后续请求报错 | 上一条超时残留未完成的 tool_call | `INVALID_CHAT_HISTORY` |

**第一轮优化（四个方向）**

1. **RAG 工具描述强化**：Agent 不走 RAG 而走高德查攻略——根因是 RAG 工具描述"搜索旅游知识库"没有说明它和高德的本质区别。改为"【首选工具】查攻略/美食/历史文化——不要用高德代替，高德只返回 POI 坐标，没有深度攻略和人文内容"。

2. **recursion_limit 硬限制**：Prompt 约束在 LangGraph 层面没有强制力。在 `create_react_agent` config 中设 `recursion_limit: 8`，Prompt 约束 + 代码硬限制双层兜底。

3. **System Prompt 精准路由**：显式告诉 Agent 什么场景用什么工具——"攻略/美食/历史→RAG，实时天气/交通→高德，混合问题→先 RAG 再 MCP"。

4. **评估脚本提速**：HITL 轮询从 3s 降到 1s，POST 超时从 10s 提到 30s。

**第二轮（GLM-5.2，完成率 45%）**

RAG 类从 2/5 提升到 4/5（✅ 工具描述优化生效），边缘 Case 保持 4/5（✅ Agent 能正确判断何时不调工具）。MCP 仍不稳定——高德公交/天气 API 的超时是外部依赖瓶颈，不是 Agent 推理问题。

### R — Result（量化结果 + 已知局限）

| 轮次 | 模型 | 完成率 | 纯RAG | 纯MCP | 混合 | 边缘 |
|------|------|--------|-------|-------|------|------|
| v1 | Qwen-3.7 | 25% | 2/5 | 0/5 | 0/5 | 3/5 |
| v2 | GLM-5.2 | 45% | 4/5 | 1/5 | 0/5 | 4/5 |

### RAG 效果对比

| Query | 不加 RAG | 加 RAG 后 |
|-------|---------|----------|
| 南京有什么特色小吃？ | 走高德搜 POI（返回 20 家餐厅名，无深度） | 调 RAG 返回《南京旅游攻略》——盐水鸭千年历史、鸭血粉丝汤起源、桂花鸭时节推荐 |
| 苏州园林哪个最值得去？ | 走高德搜"园林"POI | 调 RAG 返回拙政园/狮子林/留园对比 + 路线推荐 + 门票信息 |
| 旅行怎么省钱？ | 无工具调用，GLM 凭训练知识回答 | 调 RAG 返回知识库中的省钱攻略（机票/住宿/门票 6 个技巧） |

**结论**：RAG 让攻略类问题的回答从"泛泛而谈"变成"有具体来源的深度内容"。高德负责"哪家店在哪条街"，RAG 负责"这家店有什么历史——为什么值得去"。

### 模型选型对比

**选型标准**：工具调用准确率 > 推理效率（reasoning_token 占比）> Prompt Caching 命中率 > 完成率

| 指标 | Qwen-3.7 | GLM-5.2 | 为什么选 GLM |
|------|---------|---------|-------------|
| 平均工具调用步数 | 4.2 步 | 3.1 步 | GLM 更克制——拿到足够信息就停 |
| Reasoning Token 占比 | 38% | 18% | GLM 思考消耗少，更多 Token 用于实际回答 |
| Prompt Caching 命中率 | 0% | 65% | GLM 连续对话时复用缓存，成本降低 50-90% |
| 纯 RAG 完成率 | 2/5 | 4/5 | GLM 对"知识库 vs 实时 API"的判断更准 |

**选型不是选最强的，是选最适合 Agent 场景的**——GLM-5.2 在工具调用克制度和缓存复用上明显优于 Qwen-3.7，更适合生产环境的高频 Agent 调用。

**已知局限**：混合任务仍低完成率（下一步加 Workflow 拆分）。MCP 外部 API 超时需更激进的重试策略。20 条测试偏少（下一步扩到 50 条 + RAGAS 端到端评估）。

**面试时推荐讲的数据**：

> "第一轮完成率 25%，我分析了每条失败的 Thought-Action-Observation 链，定位了三个根因——工具描述弱导致选错工具、recursion_limit 没硬限制、HITL 轮询吃时间。修完后提到 45%，RAG 类从 2/5 提升到 4/5。混合任务仍是我下一步重点——需要 Workflow 层识别复合 Query 后拆成独立子任务并行执行。"
