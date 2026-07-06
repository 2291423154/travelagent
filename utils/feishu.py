"""
飞书 SDK 封装层
- WebSocket 连接管理
- 消息接收与发送
- 自动重连
"""

import sys
import asyncio
import logging
import json
import time
from typing import Optional, Callable, Dict, Any

# Windows 事件循环修复
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logger = logging.getLogger(__name__)


class FeishuBotClient:
    """飞书机器人客户端 — 封装 lark-oapi SDK"""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        message_handler: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.message_handler = message_handler
        self.ws_client = None
        self.api_client = None
        self._running = False
        self._reconnect_count = 0

    # ---------- 启动 / 停止 ----------

    def start(self):
        """启动飞书 WebSocket 连接（阻塞）"""
        import lark_oapi
        from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

        # 创建 API 客户端（用于发消息）
        self.api_client = lark_oapi.Client.builder() \
            .app_id(self.app_id) \
            .app_secret(self.app_secret) \
            .log_level(lark_oapi.LogLevel.INFO) \
            .build()

        # 创建事件分发器，注册 IM 消息事件
        # WebSocket 模式不需要 encrypt_key 和 verification_token，传空字符串
        handler = EventDispatcherHandler.builder(
            "", ""
        ).register_p2_im_message_receive_v1(
            self._on_message_receive
        ).build()

        # 创建 WS 客户端
        self.ws_client = lark_oapi.ws.Client(
            app_id=self.app_id,
            app_secret=self.app_secret,
            event_handler=handler,
            log_level=lark_oapi.LogLevel.INFO,
            auto_reconnect=True,
        )

        self._running = True
        logger.info(f"[飞书] 正在连接 WebSocket... (app_id={self.app_id[:8]}***)")
        self.ws_client.start()

    def stop(self):
        """停止连接"""
        self._running = False
        logger.info("[飞书] 已停止")

    # ---------- 消息接收 ----------

    def _on_message_receive(self, data):
        """处理 im.message.receive_v1 事件（SDK 传入 P2ImMessageReceiveV1 对象）"""
        try:
            # SDK 传的是 Pydantic 对象，兼容 dict 和属性访问
            def _get(obj, attr, default=None):
                if isinstance(obj, dict):
                    return obj.get(attr, default)
                return getattr(obj, attr, default)

            event = _get(data, "event", data)
            message = _get(event, "message", event)

            chat_id = _get(message, "chat_id", "")
            msg_type = _get(message, "message_type", "") or _get(message, "msg_type", "")
            msg_id = _get(message, "message_id", "")
            content_raw = _get(message, "content", "{}")
            chat_type = _get(message, "chat_type", "p2p")

            # 获取发送者 open_id
            sender = _get(event, "sender", {})
            sender_id = _get(sender, "sender_id", {})
            open_id = _get(sender_id, "open_id", "") or _get(sender_id, "user_id", "unknown")

            # 解析消息文本
            text = self._parse_content(msg_type, content_raw)

            logger.info(
                f"[飞书] 收到消息: open_id={open_id}, chat_id={chat_id}, "
                f"type={msg_type}, chat_type={chat_type}, text={text[:100] if text else '(empty)'}"
            )

            # 跳过机器人自己的消息（open_id 为空或等于 bot）
            if not open_id or open_id == "unknown":
                return

            # 回调给上层
            if self.message_handler and text:
                self.message_handler({
                    "open_id": open_id,
                    "chat_id": chat_id,
                    "msg_id": msg_id,
                    "msg_type": msg_type,
                    "chat_type": chat_type,
                    "text": text,
                })

        except Exception as e:
            logger.error(f"[飞书] 处理消息异常: {e}", exc_info=True)

    @staticmethod
    def _parse_content(msg_type: str, content_raw: str) -> str:
        """解析飞书消息内容"""
        try:
            content_json = json.loads(content_raw)
        except (json.JSONDecodeError, TypeError):
            return content_raw or ""

        if msg_type == "text":
            return content_json.get("text", "")
        elif msg_type == "post":
            # 递归提取富文本中的纯文本
            post = content_json.get("content", content_json)
            return FeishuBotClient._extract_post_text(post)
        return str(content_json)

    @staticmethod
    def _extract_post_text(post_data) -> str:
        """从飞书 post 富文本提取纯文本"""
        if isinstance(post_data, str):
            try:
                post_data = json.loads(post_data)
            except (json.JSONDecodeError, TypeError):
                return post_data

        lines = []
        if isinstance(post_data, list):
            for paragraph in post_data:
                if isinstance(paragraph, list):
                    parts = []
                    for element in paragraph:
                        if isinstance(element, dict):
                            parts.append(element.get("text", ""))
                        elif isinstance(element, str):
                            parts.append(element)
                    lines.append("".join(parts))
        elif isinstance(post_data, dict):
            for lang in ("zh_cn", "en_us", "ja_jp"):
                lang_content = post_data.get(lang, {})
                if lang_content:
                    lines.append(FeishuBotClient._extract_post_text(lang_content.get("content", [])))
                    break
        return "\n".join(lines)

    # ---------- 消息发送 ----------

    def send_text(self, chat_id: str, text: str) -> Optional[str]:
        """发送纯文本消息到指定 chat"""
        try:
            req = lark_oapi.im.v1.model.CreateMessageReq.builder() \
                .receive_id_type("chat_id") \
                .body(lark_oapi.im.v1.model.CreateMessageReqBody.builder()
                      .receive_id(chat_id)
                      .msg_type("text")
                      .content(json.dumps({"text": text}))
                      .build()) \
                .build()
            resp = self.api_client.im.v1.message.create(req)
            if resp.success():
                msg_id = resp.data.message_id
                logger.info(f"[飞书] 发送成功: msg_id={msg_id}, text={text[:50]}...")
                return msg_id
            else:
                logger.error(f"[飞书] 发送失败: code={resp.code}, msg={resp.msg}")
                return None
        except Exception as e:
            logger.error(f"[飞书] 发送异常: {e}")
            return None

    def reply_text(self, msg_id: str, text: str) -> Optional[str]:
        """回复指定消息"""
        try:
            req = lark_oapi.im.v1.model.ReplyMessageReq.builder() \
                .message_id(msg_id) \
                .body(lark_oapi.im.v1.model.ReplyMessageReqBody.builder()
                      .msg_type("text")
                      .content(json.dumps({"text": text}))
                      .build()) \
                .build()
            resp = self.api_client.im.v1.message.reply(req)
            if resp.success():
                return resp.data.message_id
            else:
                logger.error(f"[飞书] 回复失败: code={resp.code}, msg={resp.msg}")
                return None
        except Exception as e:
            logger.error(f"[飞书] 回复异常: {e}")
            return None

    def send_rich(self, chat_id: str, title: str, lines: list) -> Optional[str]:
        """发送富文本消息（post 格式）"""
        try:
            post_content = self._build_post(title, lines)
            req = lark_oapi.im.v1.model.CreateMessageReq.builder() \
                .receive_id_type("chat_id") \
                .body(lark_oapi.im.v1.model.CreateMessageReqBody.builder()
                      .receive_id(chat_id)
                      .msg_type("post")
                      .content(post_content)
                      .build()) \
                .build()
            resp = self.api_client.im.v1.message.create(req)
            if resp.success():
                return resp.data.message_id
            else:
                logger.error(f"[飞书] 发送富文本失败: code={resp.code}, msg={resp.msg}")
                return None
        except Exception as e:
            logger.error(f"[飞书] 发送富文本异常: {e}")
            return None

    @staticmethod
    def _build_post(title: str, lines: list) -> str:
        """构建飞书 post 格式 JSON"""
        content_paragraphs = []
        for line in lines:
            paragraph = [{"tag": "text", "text": line}]
            content_paragraphs.append(paragraph)

        post = {
            "zh_cn": {
                "title": title,
                "content": content_paragraphs,
            }
        }
        return json.dumps(post, ensure_ascii=False)


# 单例工厂
_feishu_client: Optional[FeishuBotClient] = None


def get_feishu_client(
    app_id: str = "",
    app_secret: str = "",
    message_handler: Callable = None,
) -> FeishuBotClient:
    """获取全局飞书客户端实例"""
    global _feishu_client
    if _feishu_client is None:
        if not app_id or not app_secret:
            from .config import Config
            app_id = app_id or Config.FEISHU_APP_ID
            app_secret = app_secret or Config.FEISHU_APP_SECRET
        _feishu_client = FeishuBotClient(
            app_id=app_id,
            app_secret=app_secret,
            message_handler=message_handler,
        )
    return _feishu_client