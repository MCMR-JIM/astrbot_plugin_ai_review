"""统一日志工具。

基于标准 logging，并兼容 AstrBot 插件日志系统（``astrbot.plugin.<name>``）：
AstrBot 会为该名称的 logger 自动接入标签、文件与控制台转发，且可独立调级别。

核心能力：
- ``get_logger()``：返回带上下文追踪的日志适配器，模块级使用即可。
- ``review_context()``：为一次审核流程绑定 request_id / 群 / 用户 / 任务，
  该流程内所有日志自动附带 ``[#abc123 群=g1 用户=u1 任务=t1]`` 前缀，
  便于按单次审核串接排查。
- ``log_review()``：输出结构化审核事件日志。
"""

from __future__ import annotations

import contextvars
import logging
import time
import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..models import ReviewLog

_PLUGIN_NAME = "astrbot_plugin_ai_review"
_LOG_NAME = f"astrbot.plugin.{_PLUGIN_NAME}"

_CURRENT: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "astrbot_ai_review_context", default=None
)


def _new_request_id() -> str:
    """生成简短的请求追踪 ID。"""
    return uuid.uuid4().hex[:6]


@contextmanager
def review_context(
    *,
    group_id: str = "",
    user_id: str = "",
    task_id: str = "",
    provider: str = "",
):
    """为一次审核流程绑定上下文，期间所有日志自动附带追踪前缀。

    支持嵌套（如被动审核内再触发规则提炼）：内层会覆盖同名字段，
    退出时自动恢复外层上下文。

    Args:
        group_id: 群号。
        user_id: 被审核用户 ID。
        task_id: 审核任务 ID。
        provider: 判定模型 Provider ID。
    """
    token = _CURRENT.set(
        {
            "request_id": _new_request_id(),
            "group_id": group_id,
            "user_id": user_id,
            "task_id": task_id,
            "provider": provider,
        }
    )
    try:
        yield
    finally:
        _CURRENT.reset(token)


class _ContextAdapter(logging.LoggerAdapter):
    """日志适配器：将当前审核上下文渲染为消息前缀。"""

    def process(self, msg: Any, kwargs: dict) -> tuple[Any, dict]:
        ctx = _CURRENT.get()
        if ctx is None:
            return msg, kwargs
        parts = [f"#{ctx['request_id']}"]
        if ctx.get("group_id"):
            parts.append(f"群={ctx['group_id']}")
        if ctx.get("user_id"):
            parts.append(f"用户={ctx['user_id']}")
        if ctx.get("task_id"):
            parts.append(f"任务={ctx['task_id']}")
        if ctx.get("provider"):
            parts.append(f"模型={ctx['provider']}")
        return f"[{' '.join(parts)}] {msg}", kwargs


def get_logger() -> "_ContextAdapter":
    """获取插件专用日志器（带上下文追踪的适配器）。

    Returns:
        日志适配器；各模块在模块顶部 ``logger = get_logger()`` 使用。
    """
    return _ContextAdapter(logging.getLogger(_LOG_NAME))


def log_review(entry: "ReviewLog") -> None:
    """输出一条结构化审核日志。

    Args:
        entry: 审核日志数据模型。
    """
    logger = get_logger()
    time_str = time.strftime(
        "%Y-%m-%d %H:%M:%S",
        time.localtime(entry.timestamp),
    )
    logger.info(
        "[审核] 时间=%s 群=%s 用户=%s 任务=%s 模型=%s 内容=%r 风险=%d 结果=%s 管理员=%s 处罚=%s 黑库=%s",
        time_str,
        entry.group_id,
        entry.user_id,
        entry.task_id,
        entry.llm_provider,
        entry.content,
        entry.risk,
        entry.review_status,
        entry.admin_id,
        entry.punishment,
        entry.blacklist_sync,
    )


def log_event(action: str, **fields: Any) -> None:
    """输出一条结构化业务事件日志，便于按事件名检索。

    Args:
        action: 事件名称（如 review_created / punishment_executed）。
        fields: 事件附带的关键字段。
    """
    parts = [f"{key}={value!r}" for key, value in fields.items() if value not in (None, "")]
    logger = get_logger()
    logger.info("[事件] %s %s", action, " ".join(parts))
