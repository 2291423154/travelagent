from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List
import time


# 定义数据模型 客户端发起的运行智能体的请求数据
class AgentRequest(BaseModel):
    # 用户唯一标识
    user_id: str
    # 会话唯一标识
    session_id: str
    # 任务唯一标识
    task_id: str
    # 用户的问题
    query: str
    # 系统提示词
    system_message: Optional[str] = ("你是一个高效助手。你可以使用工具获取信息，但必须遵守以下规则：\n"
        "1. 最多调用3次工具，之后必须基于已有信息给出最终回答\n"
        "2. 工具结果已经足够回答用户问题时，立即停止调用工具，直接回复\n"
        "3. 如果不需要工具就能回答（如闲聊、简单问答），直接回复，不要调工具\n"
        "4. 用中文简洁回复")

# 定义数据模型 客户端发起的写入长期记忆的请求数据
class LongMemRequest(BaseModel):
    # 用户唯一标识
    user_id: str
    # 写入的内容
    memory_info: str

# 定义数据模型 运行智能体后返回的响应数据
class AgentResponse(BaseModel):
    # 会话唯一标识
    session_id: str
    # 任务唯一标识
    task_id: str
    # 两个状态：completed, error
    status: str
    # 时间戳
    timestamp: float = Field(default_factory=lambda: time.time())
    # error时的提示消息
    message: Optional[str] = None
    # completed时的结果消息
    result: Optional[Dict[str, Any]] = None
    # # interrupted时的中断消息
    interrupt_data: Optional[Dict[str, Any]] = None

# 定义数据模型 客户端发起的恢复智能体运行的中断反馈请求数据
class InterruptResponse(BaseModel):
    # 用户唯一标识
    user_id: str
    # 会话唯一标识
    session_id: str
    # 任务唯一标识
    task_id: str
    # 响应类型：accept(允许调用), edit(调整工具参数，此时args中携带修改后的调用参数), response(直接反馈信息，此时args中携带修改后的调用参数)，reject(不允许调用)
    response_type: str
    # 如果是edit, response类型，可能需要额外的参数
    args: Optional[Dict[str, Any]] = None

# 定义数据模型 系统内的会话状态响应数据
class SystemInfoResponse(BaseModel):
    # 当前系统内会话总数
    sessions_count: int
    # 系统内当前活跃的用户和会话
    active_users: Optional[Dict[str, Any]] = None

# 定义数据模型 所有会话ID响应数据
class SessionInfoResponse(BaseModel):
    # 当前用户的所有session_id
    session_ids: List[str]

# 定义数据模型 所有任务ID响应数据
class TaskInfoResponse(BaseModel):
    # 当前用户会话的所有task_ids
    task_ids: List[str]

# 定义数据模型 当前最近一次更新的会话ID响应
class ActiveSessionInfoResponse(BaseModel):
    # 最近一次更新的会话ID
    active_session_id: str

# 定义数据模型 会话状态详情响应数据
class SessionStatusResponse(BaseModel):
    # 用户唯一标识
    user_id: str
    # 会话唯一标识
    session_id: Optional[str] = None
    # 任务唯一标识
    task_id: str
    # 状态：not_found, idle, running, completed, error
    status: str
    # error时的提示消息
    message: Optional[str] = None
    # 上次查询
    last_query: Optional[str] = None
    # 上次更新时间
    last_updated: Optional[float] = None
    # 上次响应
    last_response: Optional[AgentResponse] = None

