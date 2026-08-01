"""审核任务队列（内存 + KV 持久化）。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ..models import ReviewStatus, ReviewTask
from .persistence import KVStore


class ReviewQueue:
    """审核任务队列。

    任务以 task_id 为键保存在内存，并通过 KVStore 持久化（可选），
    插件重启后可由 load() 恢复。所有变更操作均为异步。
    """

    def __init__(
        self,
        store: KVStore | None = None,
        get_config: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        """初始化队列。

        Args:
            store: KV 持久化存储，为 None 时不持久化。
            get_config: 配置回调（用于读取队列上限），可接受群号参数。
        """
        self._tasks: dict[str, ReviewTask] = {}
        self._store = store
        self._get_config = get_config
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """从 KV 恢复任务并清理已过期项。"""
        if self._store is None:
            return
        raw = await self._store.get("review_tasks", {})
        if not isinstance(raw, dict):
            return
        async with self._lock:
            tasks: dict[str, ReviewTask] = {}
            for task_id, data in raw.items():
                if not isinstance(data, dict):
                    continue
                try:
                    tasks[str(task_id)] = ReviewTask.from_dict(data)
                except Exception:
                    continue
            self._tasks = tasks
            await self._cleanup_locked()

    async def _save(self) -> None:
        if self._store is None:
            return
        snapshot = {
            task_id: task.to_dict()
            for task_id, task in self._tasks.items()
        }
        await self._store.put("review_tasks", snapshot)

    async def add(self, task: ReviewTask) -> bool:
        """添加任务；超过队列上限或同用户待处理上限时返回 False。"""
        async with self._lock:
            if self._over_limit(task):
                return False
            self._tasks[task.task_id] = task
            await self._save()
            return True

    def _over_limit(self, task: ReviewTask) -> bool:
        if self._get_config is None:
            return False
        config = self._get_config(task.group_id)
        max_total = int(config.get("max_pending_total", 200))
        max_per_user = int(config.get("max_pending_per_user", 2))
        if len(self._tasks) >= max_total:
            return True
        same_user = sum(
            1
            for item in self._tasks.values()
            if item.status is ReviewStatus.PENDING
            and item.user_id
            and item.user_id == task.user_id
            and item.group_id == task.group_id
        )
        return same_user >= max_per_user

    async def get(self, task_id: str) -> ReviewTask | None:
        """按 ID 获取任务（含过期清理）。"""
        async with self._lock:
            await self._cleanup_locked()
            return self._tasks.get(task_id)

    async def list_pending(self, group_id: str | None = None) -> list[ReviewTask]:
        """列出待处理任务，按创建时间升序。"""
        async with self._lock:
            await self._cleanup_locked()
            return self._pending_locked(group_id)

    async def list_all(self, group_id: str | None = None) -> list[ReviewTask]:
        """列出全部任务（含已处理），按创建时间升序。"""
        async with self._lock:
            tasks = [
                task
                for task in self._tasks.values()
                if not group_id or task.group_id == group_id
            ]
            tasks.sort(key=lambda task: task.created_at)
            return tasks

    async def approve(
        self,
        task_id: str,
        admin_id: str,
    ) -> ReviewTask | None:
        """通过一条待处理任务。"""
        async with self._lock:
            await self._cleanup_locked()
            task = self._tasks.get(task_id)
            if task is None or task.status is not ReviewStatus.PENDING:
                return None
            task.approve(admin_id)
            await self._save()
            return task

    async def reject(
        self,
        task_id: str,
        admin_id: str,
    ) -> ReviewTask | None:
        """拒绝一条待处理任务。"""
        async with self._lock:
            await self._cleanup_locked()
            task = self._tasks.get(task_id)
            if task is None or task.status is not ReviewStatus.PENDING:
                return None
            task.reject(admin_id)
            await self._save()
            return task

    async def cleanup_expired(self) -> list[ReviewTask]:
        """清理已超时的待处理任务。"""
        async with self._lock:
            return await self._cleanup_locked()

    async def pending_count(self) -> int:
        """当前待处理任务数（先清理过期）。"""
        async with self._lock:
            await self._cleanup_locked()
            return len(self._pending_locked())

    async def _cleanup_locked(self) -> list[ReviewTask]:
        expired = []
        for task in list(self._tasks.values()):
            if task.status is ReviewStatus.PENDING and task.is_expired:
                task.mark_expired()
                expired.append(task)
                self._tasks.pop(task.task_id, None)
        if expired:
            await self._save()
        return expired

    def _pending_locked(self, group_id: str | None = None) -> list[ReviewTask]:
        tasks = [
            task
            for task in self._tasks.values()
            if task.status is ReviewStatus.PENDING
            and (not group_id or task.group_id == group_id)
        ]
        tasks.sort(key=lambda task: task.created_at)
        return tasks
