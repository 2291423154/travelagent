"""
飞书 Bot 主进程
- 连接飞书 WebSocket
- 接收用户消息 → 调用后端 Agent API
- 轮询等待结果 → 回复飞书用户
- 支持 HITL 人工审查模式

使用方式:
    python feishu_bot.py
"""

import sys
import os
import asyncio
import json
import time
import uuid
import logging
import requests
from typing import Dict, Any, Optional

# Windows 事件循环修复
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("feishu_bot")

# ===================== 配置 =====================

API_BASE_URL = "http://localhost:8001"

# 使用 open_id 前缀作为 user_id
USER_PREFIX = "feishu_"

# 轮询间隔（秒）
POLL_INTERVAL = 2

# 最大等待时间（秒）— Agent 可能需要多轮 LLM 调用
MAX_WAIT_TIME = 120

# 是否自动批准工具调用（生产环境建议 True）
AUTO_ACCEPT_TOOLS = os.getenv("FEISHU_AUTO_ACCEPT_TOOLS", "true").lower() == "true"


# ===================== Agent API 封装 =====================

def invoke_agent(user_id: str, session_id: str, query: str) -> dict:
    """调用后端 Agent API，异步执行"""
    task_id = str(uuid.uuid4())
    payload = {
        "user_id": user_id,
        "session_id": session_id,
        "task_id": task_id,
        "query": query,
    }
    resp = requests.post(
        f"{API_BASE_URL}/agent/invoke",
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    result["task_id"] = task_id  # 确保 task_id 返回
    return result


def resume_agent(user_id: str, session_id: str, task_id: str, response_type: str, args: dict = None) -> dict:
    """恢复被中断的 Agent"""
    payload = {
        "user_id": user_id,
        "session_id": session_id,
        "task_id": task_id,
        "response_type": response_type,
        "args": args,
    }
    resp = requests.post(
        f"{API_BASE_URL}/agent/resume",
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_agent_status(user_id: str, session_id: str, task_id: str) -> dict:
    """查询 Agent 任务状态"""
    resp = requests.get(
        f"{API_BASE_URL}/agent/status/{user_id}/{session_id}/{task_id}",
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


# ===================== Agent 对话逻辑 =====================

def process_query(user_id: str, text: str) -> str:
    """
    处理用户查询，等待 Agent 完成后返回最终回复文本。
    HITL 中断时自动 accept 工具调用（可配置）。
    """
    session_id = str(uuid.uuid4())

    # 1. 调用 Agent
    try:
        result = invoke_agent(user_id, session_id, text)
    except Exception as e:
        logger.error(f"调用 Agent 失败: {e}")
        return f" 抱歉，调用 Agent 服务失败：{e}"

    task_id = result.get("task_id", "")
    if not task_id:
        return " 抱歉，未能获取任务 ID，请稍后重试。"

    logger.info(f"Agent 已提交: user={user_id}, session={session_id}, task={task_id}")

    # 2. 轮询等待结果
    start_time = time.time()
    response_text = None

    while time.time() - start_time < MAX_WAIT_TIME:
        time.sleep(POLL_INTERVAL)

        try:
            status_resp = get_agent_status(user_id, session_id, task_id)
        except Exception as e:
            logger.error(f"查询状态失败: {e}")
            continue

        status = status_resp.get("status", "unknown")
        logger.info(f"状态: {status}")

        if status == "completed":
            # 提取 Agent 最终回复
            last_response = status_resp.get("last_response", {})
            if isinstance(last_response, dict):
                result_data = last_response.get("result", {})
                messages = result_data.get("messages", [])
                if messages:
                    final_msg = messages[-1]
                    content = final_msg.get("content", "")
                    if content:
                        response_text = str(content)
                        break
                # 如果没有 messages，尝试 interrupt_data
                interrupt_data = last_response.get("interrupt_data")
                if interrupt_data:
                    response_text = f"[已完成] {interrupt_data.get('description', '')}"
                    break
            if not response_text:
                response_text = "[Agent 已完成，但未返回文本内容]"
            break

        elif status == "interrupted":
            if AUTO_ACCEPT_TOOLS:
                # 自动批准工具调用
                try:
                    resume_agent(user_id, session_id, task_id, "accept")
                    logger.info(" 已自动批准工具调用，继续等待...")
                except Exception as e:
                    logger.error(f"自动批准失败: {e}")
                    return f" 工具调用需要人工审批，但自动批准失败：{e}"
            else:
                # 返回中断提示
                interrupt_data = None
                last_response = status_resp.get("last_response", {})
                if isinstance(last_response, dict):
                    interrupt_data = last_response.get("interrupt_data", {})
                tool_info = ""
                if interrupt_data:
                    action = interrupt_data.get("action_request", {}).get("action", "unknown")
                    args = interrupt_data.get("action_request", {}).get("args", {})
                    tool_info = f"\n将要调用工具: {action}\n参数: {args}"
                return (
                    f" Agent 请求工具调用审批{tool_info}\n"
                    f"请回复 yes/no/edit/response 来做出决策。\n"
                    f"(当前为非自动审批模式)"
                )

        elif status == "error":
            last_response = status_resp.get("last_response", {})
            error_msg = last_response.get("message", "未知错误") if isinstance(last_response, dict) else "未知错误"
            return f" Agent 处理出错：{error_msg}"

        elif status == "not_found":
            return " 会话不存在，请重试。"

        # running / interrupted(已处理) 继续轮询

    if response_text is None:
        # 超时
        try:
            final_status = get_agent_status(user_id, session_id, task_id)
            current = final_status.get("status", "unknown")
            return f"️ 等待超时（{MAX_WAIT_TIME}秒），当前状态: {current}。请稍后通过 status 命令查询进度。"
        except Exception:
            return f"️ 等待超时（{MAX_WAIT_TIME}秒），无法获取最终状态。"

    return response_text


# ===================== 飞书消息处理 =====================

def handle_feishu_message(msg: dict):
    """处理飞书消息 — 由 feishu.py 回调"""
    from utils.feishu import get_feishu_client

    client = get_feishu_client()
    open_id = msg.get("open_id", "unknown")
    chat_id = msg.get("chat_id", "")
    msg_id_val = msg.get("msg_id", "")
    text = msg.get("text", "").strip()

    if not text:
        return

    user_id = f"{USER_PREFIX}{open_id}"

    # 特殊命令
    if text.lower() == "status":
        client.send_text(chat_id, "📊 当前会话状态请查看后端系统。")
        return

    if text.lower() == "new":
        client.send_text(chat_id, "🆕 下次消息将自动开启新会话。")
        return

    if text.lower() == "exit":
        client.send_text(chat_id, "👋 再见！")
        return

    # 发送"处理中"提示
    client.send_text(chat_id, " 正在思考，请稍候...")

    # 调用 Agent
    reply = process_query(user_id, text)

    # 发送回复
    lines = reply.split("\n")
    if len(lines) > 20:
        # 长回复用富文本
        client.send_rich(chat_id, "Agent 回复", lines)
    else:
        client.send_text(chat_id, reply)

    logger.info(f"[Bot] 回复已发送: user={user_id}, reply_len={len(reply)}")


# ===================== 主入口 =====================

def main():
    from utils.config import Config
    from utils.feishu import get_feishu_client

    app_id = Config.FEISHU_APP_ID
    app_secret = Config.FEISHU_APP_SECRET

    if not app_id or not app_secret:
        print("=" * 60)
        print("  飞书 Bot 配置缺失！")
        print("  请设置环境变量：")
        print("    $env:FEISHU_APP_ID=\"cli_xxx\"")
        print("    $env:FEISHU_APP_SECRET=\"xxx\"")
        print("=" * 60)
        sys.exit(1)

    print("=" * 50)
    print("  ReAct Agent — 飞书机器人")
    print(f"  App ID : {app_id[:10]}***")
    print(f"  后端 API: {API_BASE_URL}")
    print(f"  自动批准工具: {AUTO_ACCEPT_TOOLS}")
    print("=" * 50)

    # 创建飞书客户端（全局单例）
    client = get_feishu_client(
        app_id=app_id,
        app_secret=app_secret,
        message_handler=handle_feishu_message,
    )

    # 启动 WebSocket（阻塞）
    try:
        client.start()
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在退出...")
        client.stop()


if __name__ == "__main__":
    main()