import os

# 自动加载项目根目录 .env（所有入口文件：Backend/Celery/Frontend/FeishuBot 都先读到 Config）
_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(_ENV_PATH):
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_PATH)
    except ImportError:
        pass


class Config:
    """统一的配置类，集中管理所有常量"""
    # 日志持久化存储
    LOG_FILE = "logfile/app.log"
    if not os.path.exists(os.path.dirname(LOG_FILE)):
        os.makedirs(os.path.dirname(LOG_FILE))
    MAX_BYTES = 5 * 1024 * 1024
    BACKUP_COUNT = 3

    # PostgreSQL数据库配置参数
    DB_URI = os.getenv("DB_URI", "postgresql://agent:agent123@localhost:5432/postgres?sslmode=disable")
    MIN_SIZE = 5
    MAX_SIZE = 10

    # Redis数据库配置参数
    REDIS_HOST = "localhost"
    REDIS_PORT = 6379
    REDIS_DB = 0
    SESSION_TIMEOUT = 300
    TTL = 3600

    CELERY_BROKER_URL = "redis://localhost:6379/0"
    TASK_TTL = 3600

    # openai:调用gpt模型,qwen:调用阿里通义千问大模型,oneapi:调用oneapi方案支持的模型,ollama:调用本地开源大模型
    LLM_TYPE = "openai"

    # 飞书机器人配置
    FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
    FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
    # 飞书 Bot 模式: 是否自动批准工具调用（生产环境建议设为 True）
    FEISHU_AUTO_ACCEPT_TOOLS = os.getenv("FEISHU_AUTO_ACCEPT_TOOLS", "true").lower() == "true"

    # API服务地址和端口
    HOST = "0.0.0.0"
    PORT = 8001
