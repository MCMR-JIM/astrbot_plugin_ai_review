"""基于皮梦云黑库插件的同步适配器。

通过 AstrBot Context 发现已加载的 astrbot_plugin_pimeng_blacklist 插件实例，
两插件互相通信（实例互调）：
- AI 审核 → 皮梦云：调用 api.add_to_blacklist 同步黑库。
- 皮梦云 → AI 审核：调用 service.is_user_blacklisted / api.check_blacklist
  查询用户是否已在黑库，供审核流程加重判定。
插件未安装或不可用时自动跳过，不影响插件正常运行（弱依赖）。
"""

from __future__ import annotations

from typing import Any

from ..models import PunishmentType, ReviewTask
from .blacklist import BlacklistAdapter

_PLUGIN_NAME = "astrbot_plugin_pimeng_blacklist"

# 建议处罚 -> 黑库等级
_SUGGESTION_TO_LEVEL = {
    PunishmentType.WARN.value: 1,
    PunishmentType.MUTE.value: 2,
    PunishmentType.KICK.value: 3,
    PunishmentType.BAN.value: 3,
    PunishmentType.BLACKLIST.value: 3,
}


class PimengBlacklistAdapter(BlacklistAdapter):
    """皮梦云黑库插件适配器（两插件实例互调）。"""

    def __init__(self, context: Any) -> None:
        """初始化适配器。

        Args:
            context: AstrBot 插件 Context 对象。
        """
        self._context = context
        self._plugin: Any = None
        self._discover()

    def _discover(self) -> None:
        """发现已加载的皮梦云黑库插件实例（Star 元数据中的 star_cls）。"""
        plugin = None
        try:
            for star in self._context.get_all_stars():
                name = (star.name or "").lower()
                if name == _PLUGIN_NAME or "pimeng" in name:
                    candidate = getattr(star, "star_cls", None)
                    if candidate is not None and hasattr(candidate, "api"):
                        plugin = candidate
                        break
        except Exception:
            plugin = None
        self._plugin = plugin

    @property
    def available(self) -> bool:
        """适配器是否可用（皮梦云插件已加载且含 api）。"""
        self._discover()
        return (
            self._plugin is not None
            and getattr(self._plugin, "api", None) is not None
        )

    def _service(self) -> Any:
        """获取皮梦云插件本地缓存服务（可能为 None）。"""
        if self._plugin is None:
            return None
        return getattr(self._plugin, "service", None)

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
        if not self.available:
            return "皮梦云黑库插件不可用，跳过黑库同步。"
        api = self._plugin.api
        try:
            result = await api.add_to_blacklist(user_id, "user", reason, level)
        except Exception as exc:
            return f"黑库同步失败：{exc!s}"
        if result and result.get("success"):
            return f"已同步至黑库（用户 {user_id}，等级 {level}）。"
        message = result.get("message") if result else "未知错误"
        return f"黑库同步失败：{message}"

    async def sync_task(self, task: ReviewTask) -> str:
        """根据审核任务同步黑库。

        Args:
            task: 已通过管理员确认的审核任务。

        Returns:
            同步结果描述文本。
        """
        if not task.user_id:
            return "无目标用户，跳过黑库同步。"
        level = _SUGGESTION_TO_LEVEL.get(task.result.suggestion, 1)
        reason = task.result.reason or f"AI 审核：{task.result.type or '违规'}"
        return await self.add_user(task.user_id, reason, level)

    async def check_user(self, user_id: str) -> dict[str, Any] | None:
        """查询用户是否在黑库（皮梦云 → AI 审核方向）。

        优先读皮梦云插件本地缓存（同步后的数据，快且不触发限流）；
        缓存不可用或未命中时回退云端实时查询。查询失败返回 None。

        Args:
            user_id: 用户 ID。

        Returns:
            命中时返回黑库记录（{"level", "reason", "added_at", "added_by"}），
            未命中或不可用时返回 None。
        """
        if not user_id or not self.available:
            return None
        record: dict[str, Any] | None = None
        # 1) 本地缓存优先
        service = self._service()
        try:
            if service is not None:
                cached = service.get_user_data(str(user_id))
                if cached is not None:
                    record = dict(cached)
        except Exception:
            record = None
        # 2) 云端实时查询（含限流与缓存，由皮梦云插件内部处理）
        try:
            api = self._plugin.api
            raw = await api.check_blacklist(str(user_id), "user")
            if raw and raw.get("success"):
                data = (raw.get("data") or {}).get("blacklist") or raw.get("data")
                if isinstance(data, dict) and data:
                    record = {
                        "level": data.get("level", 1),
                        "reason": data.get("reason", ""),
                        "added_at": data.get("added_at", ""),
                        "added_by": data.get("added_by", ""),
                        "cloud": True,
                    }
        except Exception:
            pass
        if record:
            record["user_id"] = str(user_id)
        return record
