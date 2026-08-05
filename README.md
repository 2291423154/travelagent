# TravelAgent — AI 智能旅行助手

基于 LangGraph ReAct Agent + RAG 旅游知识库 + 高德 MCP 实时数据的智能旅行助手。支持 CLI / Web / 飞书 Bot 三种交互方式，所有工具调用经过 HITL 人工审核，接入 Langfuse 可观测性平台。

## 核心特性

- **ReAct 智能推理**：Thought → Action → Observation 闭环，自主决策工具调用
- **RAG 知识库**：20 份旅游攻略文档（支持 TXT / PDF），语义切分 + Chroma 向量检索
- **高德 MCP 实时数据**：15 个地图工具（POI 搜索、路线规划、天气、地址解析等）
- **查询分类预处理**：LLM 分类查询类型 + 混合任务动态调整推理步数
- **HITL 人工审核**：accept / reject / edit / response 四种审批模式
- **三层存储**：Redis（会话状态）+ PostgreSQL（Agent 快照 + 长期记忆）+ numpy 向量索引
- **自定义工具**：Amadeus 真实酒店搜索（含价格/评分/空房）+ 酒店预订（参数校验 + 订单号）+ 实时时间查询
- **多前端**：Rich CLI + Web SPA + 飞书 Bot
- **Langfuse 可观测性**：每次 Agent 执行完整 Trace（LLM 调用/工具调用/Token 成本/耗时）
- **评估体系**：20 条测试覆盖 4 类场景，三轮模型迭代 25%→45%→80%，查询分类预处理将混合任务提升至 4/5
- **CI/CD 自动评估**：GitHub Actions 自动跑 20 条测试，Prompt 修改自动触发回归检测

## 快速启动

```bash
# 1. 启动基础设施
docker-compose -f docker/postgresql/docker-compose.yml up -d
docker-compose -f docker/redis/docker-compose.yaml up -d

# 2. 安装依赖 + 配置
pip install -r requirements.txt
cp .env.example .env  # 编辑 .env 填入你的 API Key

# 3. 构建 RAG 知识库（可选，项目已内置预构建索引）
python -m utils.rag.build_index

# 4. 三个终端分别启动
celery -A utils.tasks worker --pool=solo -l info   # Celery
python server.py                                    # Backend
python cli_chat.py                                  # Frontend

# 5. 飞书 Bot（可选）
python feishu_bot.py
```

## 技术栈

| 层级 | 技术 |
|------|------|
| Agent | LangGraph ReAct + HITL interrupt + Workflow |
| LLM | deepseek-v4-flash (OpenAI 兼容 API) |
| Embedding | text-embedding-v1 (1536维) |
| 向量检索 | Chroma（HNSW 索引 + 余弦相似度） |
| 后端 | FastAPI + Celery + Redis |
| 存储 | PostgreSQL 15 + Redis 7 |
| 酒店 API | Amadeus Self-Service (真实酒店数据) |
| MCP | 高德地图 15 个 API |
| 可观测性 | Langfuse |
| 前端 | Rich CLI / HTML SPA / 飞书 Bot |

## 更多

- [项目架构详解](PROJECT_ARCHITECTURE.md)
- [评估体系与迭代结果](PROJECT_ARCHITECTURE.md)
