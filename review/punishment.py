"""处罚策略（策略模式 + 流水线）。

处罚类型：warn / mute / kick / ban / blacklist。
每个处罚由若干可复用阶段（stage）按流水线顺序执行，
warn/mute/kick/ban 通过 PlatformExecutor 调用平台能力，
blacklist 走黑库适配器（适配器不可用或未启用时自动跳过）。
"""

from __future__ import annotations

import abc
from typing import Any

from ..models import PunishmentType, ReviewTask

# 默认处罚流水线：suggestion -> 有序阶段列表
DEFAULT_PIPELINES: dict[str, list[str]] = {
    PunishmentType.WARN.value: [PunishmentType.WARN.value],
    PunishmentType.MUTE.value: [PunishmentType.WARN.value, PunishmentType.MUTE.value],
    PunishmentType.KICK.value: [PunishmentType.WARN.value, PunishmentType.KICK.value],
    PunishmentType.BAN.value: [PunishmentType.WARN.value, PunishmentType.BAN.value],
    PunishmentType.BLACKLIST.value: [
        PunishmentType.WARN.value,
        PunishmentType.BLACKLIST.value,
    ],
}


class PlatformExecutor:
    """平台能力执行器（禁言 / 踢出 / 发送消息）。"""

    def __init__(self, context: Any) -> None:
        """初始化执行器。

        Args:
            context: AstrBot 插件 Context 对象。
        """
        self._context = context

    async def ban_user(
        self,
        platform_id: str,
        group_id: str,
        user_id: str,
        duration: int,
    ) -> str:
        """禁言用户。

        Args:
            platform_id: 平台实例 ID。
            group_id: 群号。
            user_id: 用户 ID。
            duration: 禁言时长（秒）。

        Returns:
            空字符串表示成功，否则为错误描述。
        """
        return await self._call(
            platform_id,
            "set_group_ban",
            group_id=group_id,
            user_id=user_id,
            duration=duration,
        )

    async def kick_user(
        self,
        platform_id: str,
        group_id: str,
        user_id: str,
    ) -> str:
        """踢出用户。

        Args:
            platform_id: 平台实例 ID。
            group_id: 群号。
            user_id: 用户 ID。

        Returns:
            空字符串表示成功，否则为错误描述。
        """
        return await self._call(
            platform_id,
            "set_group_kick",
            group_id=group_id,
            user_id=user_id,
        )

    async def send_message(self, session: str, text: str) -> str:
        """向指定会话发送消息。

        Args:
            session: 统一消息来源字符串（unified_msg_origin）。
            text: 消息文本。

        Returns:
            空字符串表示成功，否则为错误描述。
        """
        try:
            from astrbot.api.event import MessageChain
            from astrbot.api.message_components import Plain

            await self._context.send_message(session, MessageChain([Plain(text)]))
            return ""
        except Exception as exc:
            return f"发送消息失败: {exc!s}"

    async def _call(self, platform_id: str, action: str, **params: Any) -> str:
        """调用平台 OneBot 动作。

        Returns:
            空字符串表示成功，否则为错误描述。
        """
        adapter = self._context.get_platform_inst(platform_id)
        if adapter is None:
            return f"未找到平台实例 {platform_id}"
        client = getattr(adapter, "get_client", None)
        bot = client() if callable(client) else getattr(adapter, "bot", None)
        if bot is None or not hasattr(bot, "call_action"):
            return f"平台 {platform_id} 不支持操作 {action}"
        try:
            await bot.call_action(action=action, **params)
            return ""
        except Exception as exc:
            return f"执行 {action} 失败: {exc!s}"


class PunishmentStrategy(abc.ABC):
    """处罚策略抽象基类。"""

    name: str

    @abc.abstractmethod
    async def execute(self, task: ReviewTask, admin_id: str) -> str:
        """执行处罚。

        Args:
            task: 已通过管理员确认的审核任务。
            admin_id: 确认执行的管理员 ID。

        Returns:
            执行结果描述文本。
        """


class WarnStrategy(PunishmentStrategy):
    """警告：向群内发送警告消息。"""

    name = PunishmentType.WARN.value

    def __init__(self, executor: PlatformExecutor) -> None:
        """初始化。

        Args:
            executor: 平台能力执行器。
        """
        self._executor = executor

    async def execute(self, task: ReviewTask, admin_id: str) -> str:
        """发送警告消息。"""
        if not task.session_id:
            return "警告未发送（缺少会话信息）。"
        text = (
            f"[AI审核] 用户 {task.nickname or task.user_id}（{task.user_id}）"
            f"已被管理员警告。原因：{task.result.reason or '无'}。"
        )
        err = await self._executor.send_message(task.session_id, text)
        return "已发送警告消息。" if not err else f"警告发送失败：{err}"


class MuteStrategy(PunishmentStrategy):
    """禁言：默认 10 分钟。"""

    name = PunishmentType.MUTE.value

    def __init__(self, executor: PlatformExecutor, duration: int = 600) -> None:
        """初始化。

        Args:
            executor: 平台能力执行器。
            duration: 禁言时长（秒），下限 60。
        """
        self._executor = executor
        self._duration = max(60, int(duration))

    async def execute(self, task: ReviewTask, admin_id: str) -> str:
        """执行禁言。"""
        if not task.user_id:
            return "无目标用户，跳过禁言。"
        err = await self._executor.ban_user(
            task.platform_id,
            task.group_id,
            task.user_id,
            self._duration,
        )
        if err:
            return f"禁言失败：{err}"
        return f"已禁言 {task.nickname or task.user_id}（{task.user_id}）{self._duration // 60} 分钟。"


class KickStrategy(PunishmentStrategy):
    """踢出群聊。"""

    name = PunishmentType.KICK.value

    def __init__(self, executor: PlatformExecutor) -> None:
        """初始化。

        Args:
            executor: 平台能力执行器。
        """
        self._executor = executor

    async def execute(self, task: ReviewTask, admin_id: str) -> str:
        """执行踢出。"""
        if not task.user_id:
            return "无目标用户，跳过踢出。"
        err = await self._executor.kick_user(
            task.platform_id,
            task.group_id,
            task.user_id,
        )
        if err:
            return f"踢出失败：{err}"
        return f"已踢出 {task.nickname or task.user_id}（{task.user_id}）。"


class BanStrategy(PunishmentStrategy):
    """拉黑：映射为长期禁言（30 天）。"""

    name = PunishmentType.BAN.value
    _BAN_DURATION = 2592000  # 30 天（秒）

    def __init__(self, executor: PlatformExecutor) -> None:
        """初始化。

        Args:
            executor: 平台能力执行器。
        """
        self._executor = executor

    async def execute(self, task: ReviewTask, admin_id: str) -> str:
        """执行拉黑（长期禁言）。"""
        if not task.user_id:
            return "无目标用户，跳过拉黑。"
        err = await self._executor.ban_user(
            task.platform_id,
            task.group_id,
            task.user_id,
            self._BAN_DURATION,
        )
        if err:
            return f"拉黑失败：{err}"
        return f"已拉黑（长期禁言）{task.nickname or task.user_id}（{task.user_id}）。"


class BlacklistStrategy(PunishmentStrategy):
    """加入皮梦云黑库。"""

    name = PunishmentType.BLACKLIST.value

    def __init__(self, adapter: Any) -> None:
        """初始化。

        Args:
            adapter: BlacklistAdapter 实例，可为 None。
        """
        self._adapter = adapter

    async def execute(self, task: ReviewTask, admin_id: str) -> str:
        """同步黑库。"""
        if self._adapter is None or not self._adapter.available:
            return "黑库适配器不可用，跳过黑库同步。"
        return await self._adapter.sync_task(task)


class Punisher:
    """处罚执行器（流水线）。

    按任务建议的处罚类型选择流水线（有序阶段列表），依次执行并汇总结果。
    支持通过配置 punish_pipeline 覆盖默认流水线，便于扩展新的处罚流程。
    """

    def __init__(
        self,
        executor: PlatformExecutor,
        blacklist_adapter: Any = None,
        get_config: Any = None,
    ) -> None:
        """初始化处罚器。

        Args:
            executor: 平台能力执行器。
            blacklist_adapter: BlacklistAdapter 实例，可为 None。
            get_config: 返回配置的回调，用于读取 mute_duration 等。
        """
        self._executor = executor
        self._get_config = get_config
        self._blacklist_enabled = False
        self._mute_duration = 600
        self._stages: dict[str, PunishmentStrategy] = {
            PunishmentType.WARN.value: WarnStrategy(executor),
            PunishmentType.MUTE.value: MuteStrategy(executor, 600),
            PunishmentType.KICK.value: KickStrategy(executor),
            PunishmentType.BAN.value: BanStrategy(executor),
            PunishmentType.BLACKLIST.value: BlacklistStrategy(blacklist_adapter),
        }
        self._pipelines: dict[str, list[str]] = dict(DEFAULT_PIPELINES)
        self._sync_config()

    def _sync_config(self, group_id: str = "") -> None:
        """同步处罚相关配置（支持热加载与按群覆盖）。"""
        if self._get_config is None:
            return
        config = self._get_config(group_id)
        self._blacklist_enabled = bool(config.get("enable_blacklist", False))
        mute_duration = int(config.get("mute_duration", 600))
        if mute_duration != self._mute_duration:
            self._mute_duration = mute_duration
            self._stages[PunishmentType.MUTE.value] = MuteStrategy(
                self._executor,
                mute_duration,
            )
        raw_pipeline = config.get("punish_pipeline") or {}
        if isinstance(raw_pipeline, dict):
            override = {
                str(key): [str(item) for item in value]
                for key, value in raw_pipeline.items()
                if isinstance(value, list)
            }
            self._pipelines = {**DEFAULT_PIPELINES, **override}

    @property
    def pipelines(self) -> dict[str, list[str]]:
        """当前处罚流水线映射（副本）。"""
        return {key: list(value) for key, value in self._pipelines.items()}

    async def execute(self, task: ReviewTask, admin_id: str) -> str:
        """按任务建议的处罚类型执行整条流水线。

        Args:
            task: 已通过的审核任务。
            admin_id: 确认执行的管理员 ID。

        Returns:
            各阶段执行结果汇总。
        """
        self._sync_config(task.group_id)
        stage_names = self._pipelines.get(task.result.suggestion) or [
            task.result.suggestion
        ]
        lines = []
        for name in stage_names:
            if name == PunishmentType.BLACKLIST.value and not self._blacklist_enabled:
                lines.append("黑库同步未启用（enable_blacklist=false），跳过。")
                continue
            stage = self._stages.get(name)
            if stage is None:
                lines.append(f"[{name}] 未知处罚阶段，已跳过。")
                continue
            lines.append(await stage.execute(task, admin_id))
        return "\n".join(lines)
