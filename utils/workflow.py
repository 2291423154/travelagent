"""查询分类预处理 — 按查询类型调整 Agent 推理步数

解决混合查询（RAG + MCP）步数多、易超 recursion_limit 的瓶颈。
做法：Agent 启动前用一次轻量 LLM 调用分类查询 → 混合查询提高 recursion_limit（8→10），
给 Agent 足够的步数完成多工具协同。

注意：这不是真正的 Workflow（Workflow 是代码控制流程分叉）。
这里只做分类 + 参数调整，Agent 实际怎么选工具仍由 ReAct 循环自主决定。
"""
import logging
from typing import Literal

logger = logging.getLogger(__name__)

CLASSIFY_PROMPT = """分析用户查询，只回复一个词。

- rag_only: 只需静态知识（攻略、历史、美食文化、景点背景）
- mcp_only: 只需实时数据（天气、交通、导航、POI 搜索）
- mixed: 同时需要上述两类信息

查询：{query}

回复（rag_only / mcp_only / mixed）："""


async def classify_query(query: str, llm_chat) -> Literal["rag_only", "mcp_only", "mixed"]:
    """用一次轻量 LLM 调用分类查询类型。失败时默认走标准参数。"""
    from langchain_core.messages import HumanMessage

    try:
        prompt = CLASSIFY_PROMPT.format(query=query)
        response = await llm_chat.ainvoke([HumanMessage(content=prompt)])
        label = response.content.strip().lower()
        if "mixed" in label:
            return "mixed"
        elif "mcp" in label:
            return "mcp_only"
        else:
            return "rag_only"
    except Exception as e:
        logger.warning(f"查询分类失败（回退为 rag_only）: {e}")
        return "rag_only"


def get_recursion_limit(query_type: str) -> int:
    """根据查询类型返回合适的 recursion_limit。

    混合查询实测: 3-5步足够（RAG+MCP各1-2步），limit=8偏紧（加上思考步骤刚好卡线），
    limit=12又太松（Agent 会逐家比对路线）。10 是实测最优折中。
    """
    if query_type == "mixed":
        return 10
    return 8
