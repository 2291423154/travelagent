import os
import logging
from concurrent_log_handler import ConcurrentRotatingFileHandler
from typing import Callable
from langchain_core.tools import BaseTool, tool as create_tool
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt.interrupt import HumanInterruptConfig, HumanInterrupt
from langgraph.types import interrupt, Command
from langchain_core.tools import tool
from .config import Config

# MCP 客户端懒加载——langchain-mcp-adapters 和 mcp 包的版本兼容性不稳定，
# 导入失败时 MCP 工具不可用，但不影响 RAG / Amadeus / get_current_time 等其他工具
_MCP_AVAILABLE = False
_MCP_ERROR = None
try:
    from langchain_mcp_adapters.client import MultiServerMCPClient
    _MCP_AVAILABLE = True
except ImportError as e:
    _MCP_ERROR = str(e)


# 设置日志基本配置，级别为DEBUG或INFO
logger = logging.getLogger(__name__)
# 设置日志器级别为DEBUG
logger.setLevel(logging.DEBUG)
# logger.setLevel(logging.INFO)
logger.handlers = []  # 清空默认处理器
# 使用ConcurrentRotatingFileHandler
handler = ConcurrentRotatingFileHandler(
    # 日志文件
    Config.LOG_FILE,
    # 日志文件最大允许大小为5MB，达到上限后触发轮转
    maxBytes = Config.MAX_BYTES,
    # 在轮转时，最多保留3个历史日志文件
    backupCount = Config.BACKUP_COUNT
)
# 设置处理器级别为DEBUG
handler.setLevel(logging.DEBUG)
handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))
logger.addHandler(handler)


# 为工具添加人工审查（human-in-the-loop）功能
async def add_human_in_the_loop(
        tool: Callable | BaseTool,
        *,
        interrupt_config: HumanInterruptConfig = None,
) -> BaseTool:
    """
    为工具添加人工审查（human-in-the-loop）

    Args:
        tool: 可调用对象或 BaseTool 对象
        interrupt_config: 可选的人工中断配置

    Returns:
        BaseTool: 一个带有人工审查功能的 BaseTool 对象
    """
    # 检查传入的工具是否为 BaseTool 的实例
    if not isinstance(tool, BaseTool):
        # 如果不是 BaseTool，则将可调用对象转换为 BaseTool 对象
        tool = create_tool(tool)

    # 使用 create_tool 装饰器定义一个新的工具函数，继承原工具的名称、描述和参数模式
    @create_tool(
        tool.name,
        description=tool.description,
        args_schema=tool.args_schema
    )
    # 定义内部函数，用于处理带有中断逻辑的工具调用
    async def call_tool_with_interrupt(config: RunnableConfig, **tool_input):
        # 创建一个人为中断请求，包含工具名称、输入参数和配置
        request: HumanInterrupt = {
            "action_request": {
                "action": tool.name,
                "args": tool_input
            },
            "config": interrupt_config,
            "description": f"准备调用 {tool.name} 工具：\n- 参数为: {tool_input}\n\n是否允许继续？\n输入 'yes' 接受工具调用\n输入 'no' 拒绝工具调用\n输入 'edit' 修改工具参数后调用工具\n输入 'response' 不调用工具直接反馈信息",
        }
        # 调用 interrupt 函数，获取人工审查的响应（取第一个响应）
        response = interrupt(request)
        logger.info(f"response: {response}")

        # 检查响应类型是否为"接受"（accept）
        if response["type"] == "accept":
            logger.info("工具调用已批准，执行中...")
            logger.info(f"调用工具: {tool.name}, 参数: {tool_input}")
            try:
                # 如果接受，直接调用原始工具并传入输入参数
                tool_response = await tool.ainvoke(input=tool_input)
                logger.info(tool_response)
            except Exception as e:
                logger.error(f"工具调用失败: {e}")

        # 检查响应类型是否为"编辑"（edit）
        elif response["type"] == "edit":
            # 如果是编辑，更新工具输入参数为响应中提供的参数
            tool_input = response["args"]["args"]
            try:
                # 使用更新后的参数调用原始工具
                tool_response = await tool.ainvoke(input=tool_input)
                logger.info(tool_response)
            except Exception as e:
                logger.error(f"工具调用失败: {e}")

        # 检查响应类型是否为"拒绝"（reject）
        elif response["type"] == "reject":
            logger.info("工具调用被拒绝，等待用户输入...")
            # 直接将用户反馈作为工具的响应
            tool_response = '该工具被拒绝使用，请尝试其他方法或拒绝回答问题。'

        # 检查响应类型是否为"响应"（response）
        elif response["type"] == "response":
            # 如果是响应，直接将用户反馈作为工具的响应
            user_feedback = response["args"]
            tool_response = user_feedback

        else:
            raise ValueError(f"Unsupported interrupt response type: {response['type']}")

        return tool_response

    return call_tool_with_interrupt


# 获取工具列表 提供给第三方调用
async def get_tools():
    import uuid
    from datetime import datetime

    # 酒店搜索工具（Amadeus 真实 API）
    @tool("search_hotels", description="搜索真实酒店信息（价格、评分、空房）。输入城市名（中文）或城市代码（如'北京'或'PEK'），可选入住/离店日期。返回真实酒店列表含名称、评分、价格和可预订状态。")
    async def search_hotels(
        city: str,
        check_in: str = "",
        check_out: str = "",
    ):
        """通过 Amadeus API 搜索真实酒店数据"""
        from utils.amadeus import get_amadeus, CITY_CODES

        city_code = CITY_CODES.get(city.strip(), city.strip().upper())
        client = get_amadeus()
        results = client.search_hotels(
            city_code=city_code,
            check_in=check_in if check_in else None,
            check_out=check_out if check_out else None,
            adults=1,
        )
        return client.format_for_llm(results)

    # 酒店预定工具（参数校验 + 模拟订单号——搜索后确认预订）
    @tool("book_hotel", description="确认预订指定酒店，需提供酒店名称、入住日期、离店日期和住客姓名。请先用 search_hotels 搜索可用酒店后再调用此工具预订。")
    async def book_hotel(
        hotel_name: str,
        check_in: str,
        check_out: str,
        guest_name: str,
    ):
        """预定酒店，返回含订单号的确认信息"""
        if not hotel_name.strip():
            return "错误：酒店名称不能为空"
        if not check_in or not check_out:
            return "错误：入住日期和离店日期均需提供"
        if check_in >= check_out:
            return f"错误：入住日期({check_in})必须早于离店日期({check_out})"
        if not guest_name.strip():
            return "错误：住客姓名不能为空"
        order_id = uuid.uuid4().hex[:10].upper()
        return (
            f"预定成功！\n"
            f"订单号：HTL-{order_id}\n"
            f"酒店：{hotel_name}\n"
            f"住客：{guest_name}\n"
            f"入住：{check_in}\n"
            f"离店：{check_out}\n"
            f"状态：已确认（模拟）"
        )

    # 获取当前时间（无需 HITL，无安全风险）
    @tool("get_current_time", description="获取当前日期和时间，用于安排行程、判断航班时间、确认时差等")
    async def get_current_time() -> str:
        """返回当前日期时间字符串"""
        now = datetime.now()
        weekday = ["一", "二", "三", "四", "五", "六", "日"][now.weekday()]
        return f"当前时间：{now.strftime('%Y年%m月%d日 %H:%M:%S')}（星期{weekday}）"

    # MCP Server工具 高德地图
    # 如果 MCP 不可用（依赖版本冲突），跳过 MCP 工具，只保留 RAG + Amadeus + 时间
    if _MCP_AVAILABLE:
        try:
            client = MultiServerMCPClient({
                "amap-maps-streamableHTTP": {
                    "url": "https://mcp.amap.com/mcp?key=" + os.getenv("AMAP_MAPS_API_KEY"),
                    "transport": "streamable_http"
                }
            })
            amap_tools = await client.get_tools()
            tools = [await add_human_in_the_loop(index) for index in amap_tools]
            logger.info(f"MCP 工具加载成功: {len(tools)} 个")
        except Exception as e:
            logger.warning(f"MCP 工具加载失败: {e}，跳过 MCP 工具")
            tools = []
    else:
        logger.warning(f"MCP 不可用: {_MCP_ERROR}，跳过 MCP 工具")
        tools = []

    # RAG 知识库检索
    @tool("search_travel_knowledge", description="【首选工具】查旅游攻略、历史文化、美食推荐、景点背景、游玩路线、避坑指南、省钱技巧。当用户问'XX有什么好吃的/好玩的/值得去的/历史典故/注意事项/怎么省钱'时，必须优先使用此工具。不要用高德搜索代替——高德只返回POI坐标列表，没有深度攻略和人文内容。此工具和高德互补：此工具拿知识，高德拿实时数据。")
    async def search_travel_knowledge(query: str):
        """检索离线知识库——包含详细旅游攻略、历史文化背景、美食推荐排名、游玩路线规划、省钱技巧。返回结构化文本。"""
        from utils.rag.retriever import get_retriever
        retriever = get_retriever()
        result = retriever.format_for_llm(query, top_k=3)
        return result if result else "知识库暂无相关内容。"

    # 追加自定义工具并添加人工审查
    tools.append(await add_human_in_the_loop(search_hotels))
    tools.append(await add_human_in_the_loop(book_hotel))
    tools.append(await add_human_in_the_loop(search_travel_knowledge))
    tools.append(get_current_time)

    # 返回工具列表
    return tools
