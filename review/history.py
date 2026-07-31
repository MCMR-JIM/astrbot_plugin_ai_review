"""群聊记录内存缓存。

使用 collections.deque 维护每个群最近 N 条聊天记录，自动淘汰旧消息。
全内存存储，不落库；支持配置热加载（history_count / enable_history）。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import Any

from ..models import ChatRecord

_DEFAULT_MAXLEN = 50


class HistoryCache:
    """群聊记录缓存。

    每个群一个 deque，容量由配置 history_count 决定。
    配置热加载：每次操作前比对最新配置，容量变化时重建 deque。
    """

    def __init__(self, get_config: Callable[[], dict[str, Any]]) -> None:
        """初始化缓存。

        Args:
            get_config: 返回当前插件配置字典的回调，用于热加载。
        """
        self._get_config = get_config
        self._groups: dict[str, deque[ChatRecord]] = {}
        self._enabled = True
        self._maxlen = _DEFAULT_MAXLEN
        self._sync_config()

    def _sync_config(self) -> None:
        """同步最新配置中的容量与开关。"""
        config = self._get_config()
        self._enabled = bool(config.get("enable_history", True))
        new_maxlen = int(config.get("history_count", _DEFAULT_MAXLEN))
        new_maxlen = max(1, new_maxlen)
        if new_maxlen != self._maxlen:
            for group_id, dq in self._groups.items():
                self._groups[group_id] = deque(dq, maxlen=new_maxlen)
            self._maxlen = new_maxlen

    @property
    def enabled(self) -> bool:
        """缓存是否启用。"""
        return self._enabled

    @property
    def group_ids(self) -> list[str]:
        """当前缓存了记录的群号列表。"""
        return list(self._groups.keys())

    def add(self, record: ChatRecord) -> None:
        """添加一条聊天记录。

        Args:
            record: 要缓存的聊天记录。
        """
        self._sync_config()
        if not self._enabled:
            return
        dq = self._groups.get(record.group_id)
        if dq is None:
            dq = deque(maxlen=self._maxlen)
            self._groups[record.group_id] = dq
        dq.append(record)

    def get_recent(self, group_id: str, count: int | None = None) -> list[ChatRecord]:
        """获取某群最近若干条聊天记录。

        Args:
            group_id: 群号。
            count: 需要的条数，默认返回全部缓存。

        Returns:
            按时间正序排列的记录列表（最新的在末尾）。
        """
        self._sync_config()
        if not self._enabled:
            return []
        dq = self._groups.get(group_id)
        if not dq:
            return []
        records = list(dq)
        if count is not None and count > 0:
            records = records[-count:]
        return records

    def get_user_recent(
        self,
        group_id: str,
        user_id: str,
        count: int | None = None,
    ) -> list[ChatRecord]:
        """获取某群内指定用户最近的聊天记录。

        Args:
            group_id: 群号。
            user_id: 用户 ID。
            count: 需要的条数，默认返回全部该用户记录。

        Returns:
            该用户的记录列表，按时间正序。
        """
        records = [r for r in self.get_recent(group_id) if r.user_id == user_id]
        if count is not None and count > 0:
            records = records[-count:]
        return records

    def clear(self, group_id: str) -> None:
        """清空指定群的缓存。

        Args:
            group_id: 群号。
        """
        self._groups.pop(group_id, None)

    def clear_all(self) -> None:
        """清空全部群的缓存。"""
        self._groups.clear()
