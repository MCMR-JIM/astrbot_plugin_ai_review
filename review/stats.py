"""违规统计（KV 持久化）。"""

from __future__ import annotations

import asyncio
import time

from .persistence import KVStore


class StatsStore:
    """按群/按用户聚合的违规统计。

    数据结构：{group_id: {user_id: {count, types, approved, rejected,
    punishments, last_ts}}}，整体序列化到 KV 的 review_stats 键。
    """

    def __init__(self, store: KVStore) -> None:
        self._store = store
        self._data: dict[str, dict[str, dict]] = {}
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """从 KV 恢复统计数据。"""
        raw = await self._store.get("review_stats", {})
        if isinstance(raw, dict):
            self._data = raw

    async def _save(self) -> None:
        await self._store.put("review_stats", self._data)

    async def record_violation(
        self,
        group_id: str,
        user_id: str,
        violation_type: str = "",
    ) -> None:
        """记录一次新发现的违规（任务入队时调用）。"""
        if not user_id:
            return
        async with self._lock:
            user = self._user(group_id, user_id)
            user["count"] = int(user.get("count", 0)) + 1
            types = user.setdefault("types", {})
            type_key = str(violation_type or "unknown")
            types[type_key] = int(types.get(type_key, 0)) + 1
            user["last_ts"] = time.time()
            await self._save()

    async def record_decision(
        self,
        group_id: str,
        user_id: str,
        approved: bool,
        punishment: str = "",
    ) -> None:
        """记录一次管理员处理结果（pass/reject 时调用）。"""
        if not user_id:
            return
        async with self._lock:
            user = self._user(group_id, user_id)
            if approved:
                user["approved"] = int(user.get("approved", 0)) + 1
                if punishment:
                    punishments = user.setdefault("punishments", {})
                    key = str(punishment)
                    punishments[key] = int(punishments.get(key, 0)) + 1
            else:
                user["rejected"] = int(user.get("rejected", 0)) + 1
            user["last_ts"] = time.time()
            await self._save()

    def _user(self, group_id: str, user_id: str) -> dict:
        groups = self._data.setdefault(str(group_id), {})
        return groups.setdefault(
            str(user_id),
            {
                "count": 0,
                "types": {},
                "approved": 0,
                "rejected": 0,
                "punishments": {},
                "last_ts": 0.0,
            },
        )

    def group_summary(self, group_id: str) -> list[dict]:
        """返回某群按违规次数降序的用户统计行。"""
        groups = self._data.get(str(group_id), {})
        rows = []
        for user_id, data in groups.items():
            rows.append(
                {
                    "user_id": str(user_id),
                    "count": int(data.get("count", 0)),
                    "types": dict(data.get("types", {})),
                    "approved": int(data.get("approved", 0)),
                    "rejected": int(data.get("rejected", 0)),
                }
            )
        rows.sort(key=lambda row: row["count"], reverse=True)
        return rows

    def all_summary(self) -> dict[str, list[dict]]:
        """返回全部群的统计摘要。"""
        return {
            str(group_id): self.group_summary(group_id)
            for group_id in self._data
        }
