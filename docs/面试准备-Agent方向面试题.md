# Agent 方向实习面试 — 项目深度问答

> 基于项目：ReActAgentHILApiMultiSessionTask（生产级 Agent 智能体服务）
> 覆盖范围：Agent 架构 · LLM 原理 · 后端工程 · 数据库 · 分布式 · 安全 · 运维

---

## 目录

1. [Agent 架构与 LangGraph](#1-agent-架构与-langgraph)
2. [LLM 调用与多模型适配](#2-llm-调用与多模型适配)
3. [Human-in-the-Loop (HITL)](#3-human-in-the-loop-hitl)
4. [记忆系统：短期/长期记忆](#4-记忆系统短期长期记忆)
5. [Function Calling 与工具系统](#5-function-calling-与工具系统)
6. [MCP (Model Context Protocol)](#6-mcp-model-context-protocol)
7. [FastAPI 后端架构](#7-fastapi-后端架构)
8. [Celery 异步任务队列](#8-celery-异步任务队列)
9. [Redis 会话管理](#9-redis-会话管理)
10. [PostgreSQL 持久化](#10-postgresql-持久化)
11. [Docker 与基础设施](#11-docker-与基础设施)
12. [故障恢复与高可用](#12-故障恢复与高可用)
13. [系统设计与综合](#13-系统设计与综合)
14. [安全意识](#14-安全意识)

---

## 1. Agent 架构与 LangGraph

### Q1: 什么是 ReAct Agent？它的工作流程是怎样的？

**答：** ReAct（Reasoning + Acting）是 LangGraph 预置的 Agent 架构，核心流程：

```
用户输入 → LLM 推理(思考) → 决定是否调用工具
    ↓                              ↓
 工具执行 ← 返回工具结果 ← 是：调用工具
    ↓
 LLM 综合工具结果 → 最终回答
```

在我项目中（`utils/tasks.py:368-374`），使用 `langgraph.prebuilt.create_react_agent` 一键创建：

```python
agent = create_react_agent(
    model=llm_chat,          # LLM 模型
    tools=tools,             # 工具列表（含 HITL 包装）
    pre_model_hook=trimmed_messages_hook,  # 消息裁剪钩子
    checkpointer=checkpointer,  # 短期记忆持久化
    store=store                # 长期记忆存储
)
```

**追问：ReAct 相比 CoT (Chain-of-Thought) 有什么优势？**
- ReAct 将推理和行动交织，LLM 可以先尝试调用工具获取外部信息，再基于实际结果调整推理，形成「思考→行动→观察→思考」的循环
- 相比纯 CoT（只有推理，没有外部行动），ReAct 能访问实时数据、减少幻觉

---

### Q2: LangGraph 的 State Graph 和 Checkpoint 机制是什么？

**答：** LangGraph 用有向图（DAG）建模 Agent 流程。每个节点是一个状态转换函数，边定义了流程走向。

**Checkpoint（检查点）** 是 LangGraph 的核心容错机制：
- 每执行完一个节点，LangGraph 自动将当前 State 快照保存到 checkpointer
- 如果流程被中断（如 HITL 暂停），可以从最近的 checkpoint 恢复
- 我用 PostgreSQL 作为 checkpointer 后端（`AsyncPostgresSaver`），保证服务重启后能恢复

```python
# 恢复中断的 Agent — utils/tasks.py:502-505
result = await agent.ainvoke(
    Command(resume=command_data),
    config={"configurable": {"thread_id": task_id}}  # thread_id 关联原会话
)
```

---

### Q3: LangGraph 和 LangChain 的关系是什么？为什么要用 LangGraph？

**答：** LangChain 提供 LLM 调用的抽象层（ChatModel、Tools、Messages），LangGraph 在 LangChain 之上提供**状态机/图编排**能力。

LangGraph 解决的核心问题：
- **复杂控制流**：LangChain Chain 是线性的，Agent 需要循环+条件分支
- **状态管理**：Agent 的多轮推理需要持久化和恢复状态
- **中断恢复**：HITL 场景需要暂停等待人类输入后继续
- **流式输出**：支持 streaming 中间结果

---

## 2. LLM 调用与多模型适配

### Q4: 项目中如何实现多厂商 LLM 适配？

**答：** 在 `utils/llms.py` 中用策略模式统一接口，所有厂商都通过 `ChatOpenAI`（OpenAI 兼容接口）调用：

| LLM_TYPE | 厂商 | API 地址 | 环境变量 |
|---|---|---|---|
| `openai` | OpenAI | 自定义 BASE_URL | `OPENAI_API_KEY` |
| `qwen` | 阿里千问 | dashscope.aliyuncs.com | `DASHSCOPE_API_KEY` |
| `ollama` | 本地开源 | localhost:11434 | 无需 |
| `oneapi` | 中转代理 | 自建网关 | 硬编码 |

核心设计：所有厂商都实现了 OpenAI 兼容的 `/v1/chat/completions` 接口，因此可以用同一个 `ChatOpenAI` 客户端，只需切换 `base_url` 和 `api_key`。

---

### Q5: 如何控制 LLM 的温度（temperature）？为什么设为 0？

**答：** 在 `utils/llms.py:99` 设置 `temperature=0`：
```python
llm_chat = ChatOpenAI(
    model=config["chat_model"],
    temperature=0,  # 确定性输出
)
```

**原因：** Agent 场景需要 LLM 严格遵循指令（选择工具、提取参数），temperature=0 保证输出的确定性和可预测性。高温度适合创意写作，低温度适合需要精确性的任务。

---

### Q6: 如何处理 LLM 调用超时和重试？

**答：** 在 `utils/llms.py:100-101`：
```python
timeout=30,       # 30 秒超时
max_retries=2     # 失败后重试 2 次
```

这是 `ChatOpenAI` 内置的容错机制，避免单次 API 调用阻塞整个 Agent 流程。

---

## 3. Human-in-the-Loop (HITL)

### Q7: 什么是 Human-in-the-Loop？你项目中支持哪几种审查类型？

**答：** HITL 是在 Agent 调用工具前插入人类审批节点的机制。工具调用需要人类确认后才能执行，防止 Agent 做出破坏性操作。

我项目中支持 **4 种审查类型**（`utils/tools.py:68-74`）：

| 类型 | 行为 | 场景 |
|---|---|---|
| `accept` | 允许工具调用，原参数执行 | 用户批准预定酒店 |
| `reject` | 拒绝工具调用，Agent 需另想办法 | 参数明显错误 |
| `edit` | 修改工具参数后执行 | "把酒店名换成 xx" |
| `response` | 不调用工具，直接反馈信息 | "汉庭满了，换如家" |

---

### Q8: HITL 的技术实现原理是什么？

**答：** 核心是 LangGraph 的 `interrupt()` 函数（`utils/tools.py:76`）：

```python
response = interrupt(request)  # 暂停 Agent，等待人类响应
```

完整流程：
```
Agent 决策调用工具
    → 工具包装器触发 interrupt()
    → Agent 进入 interrupted 状态
    → 后端返回 interrupt_data 给前端（含工具名+参数）
    → 人类做出决策（accept/reject/edit/response）
    → 前端 POST /agent/resume 提交响应
    → Celery 任务用 Command(resume=...) 继续执行
    → 根据人类决策执行相应操作
```

**关键设计点：** 整个中断状态通过 Redis 存储（`utils/tasks.py:252-263`），前端通过轮询 `GET /agent/status/{user_id}/{session_id}/{task_id}` 获知是否需要人类介入。

---

### Q9: HITL 为什么比纯自主 Agent 更安全？

**答：**
1. **防止破坏性操作**：大额交易、删除数据等操作必须人类确认
2. **参数纠错**：LLM 可能提取错误参数（如酒店名 OCR 错误），人类可修正
3. **合规要求**：金融、医疗等行业要求关键决策有人类参与
4. **边界情况处理**：不可预见的场景，人类做最终判断

---

## 4. 记忆系统：短期/长期记忆

### Q10: 项目中如何管理短期记忆？为什么需要消息裁剪？

**答：** 短期记忆通过 PostgreSQL Checkpointer 持久化 LangGraph 的 State（全部聊天消息）。

**消息裁剪**（`utils/tasks.py:67-85`）解决 LLM 上下文窗口限制：

```python
def trimmed_messages_hook(state):
    trimmed_messages = trim_messages(
        messages=state["messages"],
        max_tokens=20,        # 保留最近 20 条消息
        strategy="last",      # 保留策略：最新优先
        token_counter=len,    # 简化 token 计数
        start_on="human",     # 从人类消息开始保留
        allow_partial=False   # 不保留不完整的对话对
    )
    return {"llm_input_messages": trimmed_messages}
```

这个 hook 在**每次 LLM 调用前**触发，自动裁剪历史消息。

---

### Q11: 长期记忆是如何实现读写分离的？

**答：** 长期记忆用 PostgreSQL Store（`AsyncPostgresStore`）实现键值对存储：

**写入**（`01_backendServer.py:44-80`）：
```
Namespace: ("memories", user_id)
Key:       UUID (unique memory_id)
Value:     {"data": "用户偏好信息..."}
```

**读取**（`utils/tasks.py:88-134`）：
```python
memories = await store.asearch(namespace, query="")
# 拼接所有记忆内容
long_term_info = " ".join([d.value["data"] for d in memories])
```

**应用方式**：读取的长期记忆拼接到系统提示词中，影响 Agent 行为（如记住用户说"我喜欢便宜的酒店"）。

---

## 5. Function Calling 与工具系统

### Q12: Function Calling（Tool Use）的完整流程是怎样的？

**答：** 以 `book_hotel` 工具为例：

```
1. 定义工具 Schema
   @tool("book_hotel", description="酒店预定工具")
   async def book_hotel(hotel_name: str) -> str:
       return f"成功预定了在{hotel_name}的住宿。"

2. LLM 收到用户请求"帮我订如家酒店"
3. LLM 决定调用 book_hotel(hotel_name="如家酒店")
4. Tool Calling 请求被 HITL 包装器拦截
5. interrupt() 暂停，等待人类审批
6. 人类批准后，执行 book_hotel("如家酒店")
7. 工具结果返回给 LLM，LLM 生成最终回复
```

---

### Q13: 为什么 `multiply` 工具没有加 HITL？

**答：** 在 `utils/tools.py:172`：
```python
tools.append(multiply)  # 注意：没有 await add_human_in_the_loop()
```

`multiply` 是纯计算工具（无副作用），不需要人类审批。而 `book_hotel` 和高德地图工具（有外部效果/费用）需要 HITL。

**设计原则：有副作用的工具需要审查，无副作用/幂等的工具可以自主执行。**

---

## 6. MCP (Model Context Protocol)

### Q14: 什么是 MCP？你项目中如何使用它？

**答：** MCP（Model Context Protocol）是 Anthropic 提出的标准化工具协议。允许 Agent 通过统一接口调用任意第三方服务，而不需要为每个服务写适配代码。

项目中使用 MCP 接入高德地图服务（`utils/tools.py:154-166`）：

```python
client = MultiServerMCPClient({
    "amap-maps-streamableHTTP": {
        "url": "https://mcp.amap.com/mcp?key=" + AMAP_MAPS_API_KEY,
        "transport": "streamable_http"
    }
})
amap_tools = await client.get_tools()  # 自动发现所有可用工具
```

**优势：**
- 工具即插即用，不需要了解 MCP Server 内部实现
- 支持多种传输协议（SSE、Streamable HTTP）
- 工具发现自动化（`get_tools()` 拉取服务端定义的 schema）

---

### Q15: MCP 和传统 API 集成有什么区别？

**答：**

| 维度 | 传统 API 集成 | MCP |
|---|---|---|
| 接入方式 | 为每个 API 写调用代码 | 协议标准化，客户端统一 |
| 工具发现 | 手动定义参数 schema | 自动从 Server 拉取 schema |
| 类型安全 | 需手动验证 | Server 提供 JSON Schema |
| 扩展性 | 新增 API 需改代码 | 新增 MCP Server 即可 |

---

## 7. FastAPI 后端架构

### Q16: 为什么选择 FastAPI 而不是 Flask？

**答：**
1. **原生异步**：asyncio + uvicorn，Agent 调用是 IO 密集型（LLM API），异步能大幅提升并发
2. **自动文档**：Pydantic 模型自动生成 OpenAPI/Swagger 文档
3. **类型安全**：请求/响应自动校验，减少手动验证代码
4. **生态兼容**：与 `psycopg`（asyncpg）、`redis.asyncio` 天然配合

---

### Q17: 项目中 FastAPI 的生命周期管理是怎样的？

**答：** 使用 `@asynccontextmanager` 实现 lifespan（`01_backendServer.py:84-127`）：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # === 启动阶段 ===
    app.state.session_manager = get_session_manager()  # 连接 Redis
    pool = AsyncConnectionPool(...)                      # 连接 PostgreSQL
    app.state.checkpointer = AsyncPostgresSaver(pool)    # 短期记忆
    app.state.store = AsyncPostgresStore(pool)           # 长期记忆
    yield  # ← 服务运行中
    # === 关闭阶段 ===
    await session_manager.close()
    await pool.close()
```

**关键：** 连接池在应用级别管理，所有请求共享同一套数据库连接，避免频繁创建/销毁。

---

### Q18: 如何处理 API 层的错误状态？

**答：** 分层处理：

1. **参数校验**：Pydantic 自动校验，无效请求返回 422
2. **业务逻辑**：手动抛 `HTTPException`（如 404 用户不存在、400 状态不允许恢复）
3. **任务层**：捕获异常后更新 Redis 状态为 `error`/`failed`，前端可轮询获取

---

## 8. Celery 异步任务队列

### Q19: 为什么要用 Celery？Agent 请求为什么不能同步处理？

**答：** Agent 单次调用可能耗时 10-60+ 秒（多轮 LLM 调用 + 等待 HITL），同步处理会阻塞 FastAPI 的事件循环。

**异步模式流程**（`utils/tasks.py:319-451`）：
```
1. POST /agent/invoke → 立即返回 task_id
2. Celery Worker 异步执行 Agent
3. 前端轮询 GET /agent/status/... 查询进度
4. 状态: pending → running → interrupted/completed/error
```

---

### Q20: Celery 的 broker 是什么？为什么用 Redis？

**答：** 在 `utils/tasks.py:44-49`：
```python
celery_app = Celery(
    main='01_backendServer',
    broker=Config.CELERY_BROKER_URL  # redis://localhost:6379/0
)
```

Redis 同时作为 **消息代理**（Celery broker）和**状态存储**（会话管理），一鱼两吃。对于 Agent 这种任务量不大但状态复杂的场景非常合适。高吞吐场景可换 RabbitMQ。

---

### Q21: Celery 任务中为什么要用 `asyncio.run()` 包装异步代码？

**答：** 在 `utils/tasks.py:451`：
```python
return asyncio.run(run_invoke())
```

Celery 任务函数本身是同步的（`@celery_app.task`），但 Agent 调用链全是异步的（`async def tool.ainvoke()`、`async def read_long_term_info()` 等）。用 `asyncio.run()` 在同步上下文中运行异步协程，这是 Celery + asyncio 混合使用的常见模式。

---

## 9. Redis 会话管理

### Q22: 你的 Redis 会话管理器用了什么数据结构？为什么？

**答：** 在 `utils/redis.py` 中使用了三种 Redis 数据结构：

| 数据结构 | Key 模式 | 用途 |
|---|---|---|
| **String (JSON)** | `session:{user_id}:{session_id}:{task_id}` | 会话详情（状态、最后响应等） |
| **Set** | `user_sessions:{user_id}` | 用户的所有会话 ID 索引 |
| **Set** | `task_mapping:{user_id}:{session_id}` | 会话的所有任务 ID 索引 |

**设计理由：**
- String(JSON) 存详情是因为会话数据需要**原子更新**为整体
- Set 存索引是因为需要 **O(1) 去重**和快速查找
- TTL 自动过期，避免僵尸会话堆积

---

### Q23: Redis 中的数据过期策略是什么？

**答：** 分层 TTL 设计（`utils/config.py:22-26`）：
- `SESSION_TIMEOUT = 300` 秒：会话默认过期时间
- `TTL = 3600` 秒：创建/更新会话时的默认 TTL
- `TASK_TTL = 3600` 秒：任务状态独立过期时间

每次 `update_session` 刷新 TTL，活跃会话不会过期。前端可通过动态修改 TTL 参数控制会话生命周期。

---

### Q24: 为什么要做惰性清理（lazy cleanup）？

**答：** 在 `utils/redis.py:231-288`，`cleanup_user_tasks()` 在每次查询前运行，检查 Set 索引中的 session_id 对应的实际数据是否还在（可能已被 TTL 删除），不存在的则从索引中清理。

**设计选择：** 不跑定时任务（简单可维护），而是在查询时惰性清理。损失很小（每次 O(n) 扫描当前用户的会话），换来零运维复杂度。

---

## 10. PostgreSQL 持久化

### Q25: 项目中 PostgreSQL 承担了什么角色？

**答：**

| 角色 | 组件 | 说明 |
|---|---|---|
| 短期记忆 Checkpointer | `AsyncPostgresSaver` | 保存 LangGraph 状态图快照 |
| 长期记忆 Store | `AsyncPostgresStore` | 用户偏好等跨会话记忆 |

**为什么不用 Redis 存 Checkpoint？**
- Checkpoint 数据量大（整个对话历史 + State），需要持久化不丢失
- PostgreSQL 支持事务 ACID，保证状态一致性
- Redis 适合热数据（会话状态），PG 适合冷/温数据（记忆）

---

### Q26: 连接池的大小是如何决定的？

**答：** 在 `utils/config.py:15-16`：
```python
MIN_SIZE = 5   # 最小空闲连接
MAX_SIZE = 10  # 最大连接数
```

10 个连接对于 Agent 服务足够——因为 Agent 是 IO 密集型（等 LLM API），实际数据库操作少。连接数可按公式估算：`(核心数 × 2) + 磁盘数`。

---

## 11. Docker 与基础设施

### Q27: 为什么要用 Docker 运行数据库？

**答：**
1. **环境一致性**：开发/测试/生产同一镜像
2. **隔离性**：PostgreSQL/Redis 与 Python 代码环境隔离
3. **快速启动**：`docker-compose up -d` 两条命令即可
4. **可复现**：别人拿到代码也能跑

---

### Q28: docker-compose.yml 中的健康检查有什么作用？

**答：** 在 `docker/postgresql/docker-compose.yml` 中：
```yaml
healthcheck:
  test: ["CMD", "pg_isready", "-U", "nange"]
  interval: 10s
  timeout: 5s
  retries: 5
```

**作用：**
- Docker 知道容器是否真正 ready（不只是进程启动）
- 配合 `depends_on` 确保依赖顺序
- 生产环境配合编排工具自动重启不健康容器

---

## 12. 故障恢复与高可用

### Q29: 客户端故障恢复是如何实现的？

**答：** 流程（README 中描述）：

```
1. 用户 test2 输入"上海天气如何？" → 会话进行中
2. 客户端意外崩溃
3. 用户重新启动客户端，输入 user_id = test2
4. 后端查找该用户最近活跃的 session_id
5. 自动恢复到中断前的会话状态
```

**实现关键：** 会话状态全部存 Redis，客户端完全无状态。`GET /agent/active/sessionid/{user_id}` 返回最近会话。

---

### Q30: 服务端故障恢复是什么？

**答：** LangGraph checkpointer 机制——Agent 执行的每一步 State 都持久化到 PostgreSQL。即使 FastAPI 或 Celery Worker 崩溃重启，可以从最近的 checkpoint 恢复：

```python
config={"configurable": {"thread_id": task_id}}
```

相同的 `thread_id` 让恢复的 Agent 定位到同一个 State 图。

---

### Q31: 如何保证消息系统（Celery + Redis）的可靠性？

**答：**
1. Celery 任务结果写入 Redis `task:{task_id}`，带 TTL 防止泄漏
2. 任务失败时更新状态为 `failed` + 错误信息
3. 前端轮询可获取最终状态（成功/失败/中断）
4. Redis 和 PostgreSQL 分别持久化不同层面数据，不单点依赖

---

## 13. 系统设计与综合

### Q32: 如果 QPS 从 10 提升到 1000，你的系统需要做哪些改造？

**答：**

1. **Celery**：增加 Worker 数量和并发数（`--concurrency=N`）
2. **Redis**：单实例 → 哨兵/集群模式
3. **PostgreSQL**：读写分离（主从复制），Checkpoint 读走从库
4. **FastAPI**：多实例 + Nginx 负载均衡
5. **LLM API**：增加重试、熔断、限流（Token Bucket）
6. **连接池**：扩 PostgreSQL 连接池 MAX_SIZE
7. **监控**：加 Prometheus + Grafana 监控 API 延迟和任务队列长度

---

### Q33: 当前项目最大的工程缺陷是什么？你会如何改进？

**答：**

1. **没有 API 认证**：`/agent/invoke` 等端点无鉴权，任何人都可调用。应加 JWT/OAuth2
2. **LLM API Key 部分硬编码**：`oneapi` 的 key 写在源码里（`utils/llms.py:41`）。应用环境变量或 Vault
3. **02_frontendServer.py 为空**：缺少前端 demo
4. **没有测试**：缺少单元测试和集成测试
5. **日志文件轮转策略**：5MB 过小，生产环境应设 50-100MB
6. **Celery 结果后端**：当前只用 Redis，生产可加数据库持久化任务结果

---

### Q34: 为什么 Agent 系统选择异步任务模式而不是流式输出（SSE/WebSocket）？

**答：** 两种模式各有适用场景：

| 模式 | 优点 | 缺点 |
|---|---|---|
| 异步任务+轮询 | 容错性强，支持 HITL 暂停等待 | 用户需轮询，延迟较高 |
| SSE 流式 | 实时反馈，体验好 | 不支持长时间暂停（HITL），连接易断 |

本项目选择异步模式因为 HITL 场景下 Agent 可能被中断数分钟甚至更长，SSE 连接难以维持。

---

### Q35: 描述一下从用户输入到 Agent 返回结果的完整数据流

```
用户 ("帮我订如家酒店")
  ↓
前端 POST /agent/invoke {user_id, session_id, task_id, query, system_message}
  ↓
FastAPI: 创建/更新会话 → invoke_agent_task.delay() → 立即返回 task_id
  ↓
Celery Worker: 接收任务
  ├─ AsyncConnectionPool ← PostgreSQL
  ├─ AsyncPostgresSaver ← Checkpoint
  ├─ AsyncPostgresStore ← 长期记忆
  ├─ get_llm("qwen")    ← LLM Client
  ├─ get_tools() ← 工具列表（含 MCP）
  └─ create_react_agent(...) → agent.ainvoke()
      ↓
  LLM 推理: "需要调用 book_hotel(hotel_name='如家酒店')"
      ↓
  HITL 包装器: interrupt() → 暂停 → Redis 写入 interrupted 状态
      ↓
  前端轮询: GET /agent/status/... → status="interrupted"
      ↓
  人类: 输入 "yes" (accept)
      ↓
  前端 POST /agent/resume {type: "accept", args: {}}
      ↓
  Celery: Command(resume=...) → Agent 继续
      ↓
  book_hotel("如家酒店") 执行 → "成功预定了在如家酒店的住宿。"
      ↓
  LLM 综合: "已经为您预定了如家酒店的住宿！"
      ↓
  Redis: task 状态 → "completed", result → AgentResponse
      ↓
  前端轮询: status="completed" → 显示结果
```

---

## 14. 安全意识

### Q36: 如果你在面试中被问到「你项目中考虑了哪些安全问题」，你怎么回答？

**答：**

**已做的：**
1. **HITL 人工审查**：有副作用的工具调用需要人类确认（防止 Agent 擅自执行）
2. **Pydantic 输入校验**：所有 API 请求自动校验类型和必填字段
3. **数据库密码配置化**：通过环境变量覆盖（`DB_URI`），不硬编码
4. **Docker 隔离**：基础设施容器化，不暴露到公网

**应做但未做的（可展示安全意识）：**
1. API 鉴权（JWT/OAuth2）
2. LLM prompt injection 防护（用户输入可能构造恶意 prompt）
3. Rate limiting（防止 API 滥用）
4. API key 凭据管理（Vault/环境变量注入，勿硬编码）
5. SQL 注入防护（使用参数化查询—psycopg 默认安全）
6. CORS 配置（限制允许的来源域名）

---

## 快速自测清单

面试前可以用这些问题自我检查：

- [ ] 能画出 Agent 从接收到返回的完整数据流图
- [ ] 能解释 ReAct 循环的每一步发生了什么
- [ ] 能说出 LangGraph interrupt() 的实现原理
- [ ] 能比较同步 vs 异步、Celery vs SSE 的优劣
- [ ] 能说出 Redis 每种数据结构的选型理由
- [ ] 能解释消息裁剪的必要性和策略
- [ ] 能说出至少 3 个项目的改进方向
- [ ] 能解释 MCP 协议的价值
- [ ] 能回答「为什么用 Docker Compose 而不是 k8s」
- [ ] 能在白板上画出系统架构图

---

> 📝 本文档基于 `ReActAgentHILApiMultiSessionTask` 项目源码分析生成。
> 建议配合项目源码阅读，并尝试自己回答后再看答案。