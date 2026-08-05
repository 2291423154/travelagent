#!/bin/bash
# TravelAgent CI 评估启动脚本
# 启动完整服务栈 → 等就绪 → 跑评估 → 清理
set -e

echo "=== TravelAgent CI 评估 ==="

# 1. 等待 PostgreSQL 就绪
echo "[1/5] 等待 PostgreSQL..."
for i in $(seq 1 30); do
  if pg_isready -h localhost -p 5432 -U agent 2>/dev/null; then
    echo "  PostgreSQL 就绪"
    break
  fi
  sleep 1
done

# 2. 等待 Redis 就绪
echo "[2/5] 等待 Redis..."
for i in $(seq 1 10); do
  if redis-cli -h localhost ping 2>/dev/null; then
    echo "  Redis 就绪"
    break
  fi
  sleep 1
done

# 3. 初始化数据库 + 构建 RAG 索引
echo "[3/5] 初始化..."
python -c "
from utils.rag.retriever import RAGRetriever
from utils.llms import get_llm
print('LLM + Embedding 初始化成功')
"
python -m utils.rag.build_index 2>&1 | tail -3

# 4. 启动 Celery Worker（后台）
echo "[4/5] 启动 Celery Worker..."
celery -A utils.tasks worker --pool=solo --loglevel=warning &
CELERY_PID=$!
sleep 3
echo "  Celery Worker PID=$CELERY_PID"

# 5. 启动 FastAPI Server（后台）
echo "[5/5] 启动 FastAPI Server..."
python server.py &
SERVER_PID=$!

# 等待服务器就绪
echo "  等待服务器就绪..."
for i in $(seq 1 30); do
  if curl -s http://localhost:8001/docs > /dev/null 2>&1; then
    echo "  服务器就绪"
    break
  fi
  sleep 2
done

# ── 运行评估 ──
echo ""
echo "=== 开始评估 ==="
python -m tests.eval_ci --json-only
EVAL_EXIT=$?

# ── 清理 ──
echo ""
echo "=== 清理 ==="
kill $SERVER_PID 2>/dev/null || true
kill $CELERY_PID 2>/dev/null || true
echo "服务已停止"

exit $EVAL_EXIT
