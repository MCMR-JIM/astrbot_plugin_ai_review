"""审核任务队列（内存）。

支持：查看待审核、查看详情、通过、拒绝、超时自动失效。
审核记录保留日志（由 workflow 负责落日志）。
"""

from __future__ import annotations

from ..models import ReviewStatus, ReviewTask


class ReviewQueue:
    """审核任务队列。

    全部存储于内存，key 为任务 ID。
    """

    def __init__(self) -> None:
        self._tasks: dict[str, ReviewTask] = {}

    @property
    def pending_count(self) -> int:
        """当前待处理任务数。"""
        self.cleanup_expired()
        return len(self._pending())

    def add(self, task: ReviewTask) -> None:
        """添加一条审核任务。

        Args:
            task: 审核任务。
        """
        self._tasks[task.task_id] = task

    def get(self, task_id: str) -> ReviewTask | None:
        """按 ID 获取任务（含过期清理）。

        Args:
            task_id: 任务 ID。

        Returns:
            任务对象；不存在时返回 None。
        """
        self.cleanup_expired()
        task = self._tasks.get(task_id)
        return task

    def list_pending(self, group_id: str | None = None) -> list[ReviewTask]:
        """列出待处理任务，按创建时间升序。

        Args:
            group_id: 指定群号时只返回该群任务。

        Returns:
            待处理任务列表。
        """
        self.cleanup_expired()
        return self._pending(group_id)

    def list_all(self, group_id: str | None = None) -> list[ReviewTask]:
        """列出全部任务，按创建时间升序。

        Args:
            group_id: 指定群号时只返回该群任务。

        Returns:
            全部任务列表。
        """
        tasks = [t for t in self._tasks.values() if not group_id or t.group_id == group_id]
        tasks.sort(key=lambda t: t.created_at)
        return tasks

    def approve(self, task_id: str, admin_id: str) -> ReviewTask | None:
        """通过一条待处理任务。

        Args:
            task_id: 任务 ID。
            admin_id: 处理的管理员 ID。

        Returns:
            更新后的任务；不存在或已处理时返回 None。
        """
        task = self.get(task_id)
        if task is None or task.status is not ReviewStatus.PENDING:
            return None
        task.approve(admin_id)
        return task

    def reject(self, task_id: str, admin_id: str) -> ReviewTask | None:
        """拒绝一条待处理任务。

        Args:
            task_id: 任务 ID。
            admin_id: 处理的管理员 ID。

        Returns:
            更新后的任务；不存在或已处理时返回 None。
        """
        task = self.get(task_id)
        if task is None or task.status is not ReviewStatus.PENDING:
            return None
        task.reject(admin_id)
        return task

    def cleanup_expired(self) -> list[ReviewTask]:
        """清理已超时的待处理任务。

        Returns:
            本次被标记为失效的任务列表。
        """
        expired = []
        for task in list(self._tasks.values()):
            if task.status is ReviewStatus.PENDING and task.is_expired:
                task.mark_expired()
                expired.append(task)
                self._tasks.pop(task.task_id, None)
        return expired

    def _pending(self, group_id: str | None = None) -> list[ReviewTask]:
        """按创建时间升序返回待处理任务。"""
        tasks = [
            t
            for t in self._tasks.values()
            if t.status is ReviewStatus.PENDING and (not group_id or t.group_id == group_id)
        ]
        tasks.sort(key=lambda t: t.created_at)
        return tasks
