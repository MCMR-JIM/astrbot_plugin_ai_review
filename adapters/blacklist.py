"""皮梦云黑库同步适配器（抽象接口）。

插件不得直接依赖 astrbot_plugin_pimeng_blacklist。
通过该接口接入黑库同步，具体实现见 adapters/pimeng.py。
"""

from __future__ import annotations

import abc

from ..models import ReviewTask


class BlacklistAdapter(abc.ABC):
    """黑库同步适配器抽象基类。"""

    @property
    @abc.abstractmethod
    def available(self) -> bool:
        """适配器当前是否可用。"""

    @abc.abstractmethod
    async def add_user(
        self,
        user_id: str,
        reason: str,
        level: int,
    ) -> str:
        """将用户加入黑库。

        Args:
            user_id: 用户 ID。
            reason: 加入原因。
            level: 违规等级（1~4）。

        Returns:
            同步结果描述文本。
        """

    @abc.abstractmethod
    async def sync_task(self, task: ReviewTask) -> str:
        """根据审核任务同步黑库（按建议处罚）。

        Args:
            task: 已通过管理员确认的审核任务。

        Returns:
            同步结果描述文本。
        """
