"""消息过滤与冷却管理。

从 workflow 拆出：被动/主动审核的前置过滤、用户审核冷却表（KV 持久化）、
聊天记录裁剪等纯辅助函数，保持 workflow 聚焦于审核流程编排。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from ..config import safe_int
from ..models import ChatRecord
from .persistence import KVStore

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

_FILTER_ROLES = ("admin", "owner")
_MAX_COOLDOWN_ENTRIES = 1024


class CooldownManager:
    """用户审核冷却表（内存 + KV 持久化）。

    同一用户两次自动审核的最小间隔，避免连续触发 AI 调用。
    """

    def __init__(
        self,
        get_config: Callable[[str], dict[str, Any]],
        store: KVStore | None = None,
    ) -> None:
        """初始化冷却管理器。

        Args:
            get_config: 返回当前配置字典的回调，可接受群号参数。
            store: KV 持久化存储，为 None 时不持久化。
        """
        self._get_config = get_config
        self._store = store
        self._cooldowns: dict[str, float] = {}

    async def load_state(self) -> None:
        """从 KV 恢复冷却表。"""
        if self._store is None:
            return
        raw = await self._store.get("cooldowns", {})
        if isinstance(raw, dict):
            self._cooldowns = {
                str(key): float(value)
                for key, value in raw.items()
                if isinstance(value, (int, float))
            }

    def in_cooldown(self, group_id: str, user_id: str) -> bool:
        """判断用户是否处于审核冷却中。

        Args:
            group_id: 群号。
            user_id: 用户 ID。

        Returns:
            冷却中返回 True。
        """
        key = self._key(group_id, user_id)
        last = self._cooldowns.get(key)
        if last is None:
            return False
        cooldown = safe_int(self._get_config(group_id).get("cooldown"), 300)
        return time.time() - last < cooldown

    async def touch(self, group_id: str, user_id: str) -> None:
        """记录用户最近的审核时间（设置冷却起点）。"""
        if len(self._cooldowns) > _MAX_COOLDOWN_ENTRIES:
            self._cleanup()
        self._cooldowns[self._key(group_id, user_id)] = time.time()
        if self._store is not None:
            await self._store.put("cooldowns", self._cooldowns)

    def _cleanup(self) -> None:
        """清理已过期的冷却记录，避免字典无限增长。"""
        cooldown = safe_int(self._get_config().get("cooldown"), 300)
        now = time.time()
        self._cooldowns = {
            key: ts
            for key, ts in self._cooldowns.items()
            if now - ts < cooldown
        }

    @staticmethod
    def _key(group_id: str, user_id: str) -> str:
        return f"{group_id}:{user_id}"


class MessageFilters:
    """被动/主动审核前置过滤。"""

    def __init__(
        self,
        get_config: Callable[[str], dict[str, Any]],
        cooldown: CooldownManager,
    ) -> None:
        """初始化过滤器。

        Args:
            get_config: 返回当前配置字典的回调，可接受群号参数。
            cooldown: 冷却管理器实例。
        """
        self._get_config = get_config
        self._cooldown = cooldown

    def review_mode(self, group_id: str = "") -> str:
        """当前触发模式（支持按群覆盖）。"""
        return str(self._get_config(group_id).get("review_mode", "both"))

    def should_skip(self, event: "AstrMessageEvent") -> tuple[bool, str]:
        """被动审核前置过滤。

        Returns:
            (是否跳过, 原因)。
        """
        sender_id = event.get_sender_id()
        content = event.get_message_outline()
        if not sender_id or sender_id == event.get_self_id():
            return True, "机器人消息"
        role = str(getattr(event, "role", "member"))
        if role in _FILTER_ROLES or event.is_admin():
            return True, "管理员/群主"
        config = self._get_config(event.get_group_id())
        if sender_id in [str(u) for u in config.get("whitelist", [])]:
            return True, "白名单用户"
        if self._cooldown.in_cooldown(event.get_group_id(), sender_id):
            return True, "冷却中"
        if not content or not content.strip():
            return True, "空消息"
        if len(content.strip()) < safe_int(config.get("min_msg_len"), 2):
            return True, "过短消息"
        return False, ""

    def should_skip_target(
        self,
        event: "AstrMessageEvent",
        target_user_id: str,
    ) -> tuple[bool, str]:
        """主动审核前置过滤（仅过滤机器人/白名单/冷却）。

        Returns:
            (是否跳过, 原因)。
        """
        if not target_user_id or target_user_id == event.get_self_id():
            return True, "目标无效或为机器人"
        config = self._get_config(event.get_group_id())
        if target_user_id in [str(u) for u in config.get("whitelist", [])]:
            return True, "目标在白名单"
        if self._cooldown.in_cooldown(event.get_group_id(), target_user_id):
            return True, "目标冷却中"
        return False, ""


def trim_records(
    records: list[ChatRecord],
    max_chars: int,
    max_msg_chars: int,
) -> list[ChatRecord]:
    """按字符预算裁剪聊天记录，控制发给 AI 的 token 量。

    优先保留最新消息；单条消息过长时截断；超过总预算后丢弃更早的记录。

    Args:
        records: 原始聊天记录（时间正序）。
        max_chars: 聊天记录总字符预算。
        max_msg_chars: 单条消息字符上限。

    Returns:
        裁剪后的记录列表（时间正序）。
    """
    if max_chars <= 0:
        return []
    trimmed: list[ChatRecord] = []
    total = 0
    for record in reversed(records):
        content = record.content
        if max_msg_chars > 0 and len(content) > max_msg_chars:
            content = content[:max_msg_chars] + "…"
        cost = len(content) + len(record.nickname) + len(record.user_id)
        if total + cost > max_chars:
            break
        total += cost
        trimmed.append(
            ChatRecord(
                timestamp=record.timestamp,
                nickname=record.nickname,
                user_id=record.user_id,
                content=content,
                group_id=record.group_id,
            )
        )
    trimmed.reverse()
    return trimmed


def to_record(event: "AstrMessageEvent", group_id: str) -> ChatRecord:
    """将消息事件转换为聊天记录。"""
    # AstrMessageEvent 没有 created_at 属性，时间戳来自 message_obj.timestamp
    message_obj = getattr(event, "message_obj", None)
    timestamp = getattr(message_obj, "timestamp", None)
    if timestamp is None:
        timestamp = time.time()
    return ChatRecord(
        timestamp=float(timestamp),
        nickname=event.get_sender_name(),
        user_id=event.get_sender_id(),
        content=event.get_message_outline(),
        group_id=group_id,
    )


def user_content(records: list[ChatRecord], user_id: str) -> str:
    """提取目标用户的最近发言摘要用于日志。"""
    if not user_id:
        return ""
    texts = [
        r.content for r in records[-5:] if r.user_id == user_id and r.content
    ]
    return " | ".join(texts[-3:])
