"""统一日志工具。

基于 AstrBot 日志系统，提供插件模块级 logger 与结构化审核日志输出。
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import ReviewLog

_LOG_NAME = "astrbot_plugin_ai_review"


def get_logger() -> logging.Logger:
    """获取插件专用日志器。

    Returns:
        插件日志 Logger 实例。
    """
    return logging.getLogger(_LOG_NAME)


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
        "[审核] 时间=%s 群=%s 用户=%s 内容=%r 风险=%d 结果=%s 管理员=%s 处罚=%s 黑库=%s",
        time_str,
        entry.group_id,
        entry.user_id,
        entry.content,
        entry.risk,
        entry.review_status,
        entry.admin_id,
        entry.punishment,
        entry.blacklist_sync,
    )
