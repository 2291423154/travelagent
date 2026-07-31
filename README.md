# TravelAgent — AI 智能旅行助手

基于 LangGraph ReAct Agent + RAG 旅游知识库 + 高德 MCP 实时数据的智能旅行助手。支持 CLI / Web / 飞书 Bot 三种交互方式，所有工具调用经过 HITL 人工审核。

## 核心特性

- **ReAct 智能推理**：Thought → Action → Observation 闭环，自主决策工具调用
- **RAG 知识库**：9 本专业旅游攻略 PDF → 147 chunks → Chroma 语义检索
- **高德 MCP 实时数据**：15 个地图工具（天气/交通/POI/导航/距离）
- **HITL 人工审核**：accept / reject / edit / response 四种审批模式
- **三层存储**：Redis（会话状态）+ PostgreSQL（Agent 快照）+ Chroma（向量检索）
- **多前端**：Rich CLI + Web SPA + 飞书 Bot
- **评估体系**：20 条测试覆盖 4 类场景，两轮迭代从 25% → 45%

## 快速启动

```bash
# 1. 启动基础设施
docker-compose -f docker/postgresql/docker-compose.yml up -d
docker-compose -f docker/redis/docker-compose.yaml up -d

# 2. 配置 API Key
cp .env.example .env  # 编辑 .env 填入你的 Key

# 3. 构建 RAG 知识库
#    项目自带一份示例文档 docs/sample_travel_guide.txt
#    把你自己的旅游攻略 PDF 放入 utils/rag/documents/ 即可扩展
pip install chromadb PyPDF2 python-dotenv lark-oapi
python -m utils.rag.build_index

# 4. 三个终端分别启动
python -m celery -A utils.tasks worker --pool=solo -l info   # Celery
python server.py                                               # Backend
python cli_chat.py                                             # Frontend

# 5. 飞书 Bot（可选）
python feishu_bot.py
```

## 技术栈

| 层级 | 技术 |
|------|------|
| Agent | LangGraph ReAct + HITL interrupt |
| LLM | GLM-5.2 / Qwen-3.7 (阿里百炼) |
| Embedding | qwen3.7-text-embedding (1024维) |
| 向量库 | Chroma (HNSW 索引) |
| 后端 | FastAPI + Celery + Redis |
| 存储 | PostgreSQL 15 + Redis 7 |
| MCP | 高德地图 15 个 API |
| 前端 | Rich CLI / HTML SPA / 飞书 Bot |

## 更多

- [项目架构详解](PROJECT_ARCHITECTURE.md)
- [评估体系 (STAR)](PROJECT_ARCHITECTURE.md#八评估体系与迭代优化star-格式)
