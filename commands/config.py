"""插件配置命令（mixin，由 main.py 的 Star 继承注册）。

命令（管理员权限）：
- /reviewconfig：查看当前配置
- /reviewconfig <key> <value>：修改配置并热加载持久化
"""

from __future__ import annotations

from astrbot.api.event import filter, AstrMessageEvent

from ..config import DEFAULT_CONFIG


class ConfigCommandMixin:
    """/reviewconfig 命令实现。"""

    @filter.command("reviewconfig")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_reviewconfig(
        self,
        event: AstrMessageEvent,
        key: str = "",
        value: str = "",
    ):
        """查看或修改插件配置。"""
        if not key:
            yield event.plain_result(self._format_config())
            return
        if key == "group":
            yield event.plain_result(
                await self._handle_group_override(event)
            )
            return
        # 命令参数按空白切分，JSON 等多词值可能被截断；
        # 优先从原始消息中重建 key 之后的完整内容。
        raw = (getattr(event, "message_str", "") or "").strip()
        prefix = f"/reviewconfig {key}"
        pos = raw.find(prefix)
        if pos != -1:
            reconstructed = raw[pos + len(prefix):].strip()
            if reconstructed:
                value = reconstructed
        ok, message = await self.config.set_value(key, value)
        prefix = "✅ 已更新：" if ok else "❌ "
        yield event.plain_result(prefix + message)

    async def _handle_group_override(self, event: AstrMessageEvent) -> str:
        """处理 /reviewconfig group ... 按群覆盖配置。"""
        raw = (getattr(event, "message_str", "") or "").strip()
        prefix = "/reviewconfig group"
        pos = raw.find(prefix)
        rest = raw[pos + len(prefix):].strip() if pos != -1 else ""
        store = getattr(self, "_kv", None)
        if store is None:
            return "❌ 持久化存储不可用，无法管理按群覆盖配置。"
        parts = rest.split()
        if not parts:
            return self._format_group_overrides("")
        group_id = parts[0]
        if len(parts) == 1:
            return self._format_group_overrides(group_id)
        if parts[1] == "reset":
            ok, message = await self.config.clear_override(store, group_id)
            return ("✅ " if ok else "❌ ") + message
        if len(parts) < 3:
            return f"❌ 用法：/reviewconfig group {group_id} <key> <value>"
        key = parts[1]
        value = " ".join(parts[2:])
        ok, message = await self.config.set_override(
            store,
            group_id,
            key,
            value,
        )
        return ("✅ " if ok else "❌ ") + message

    def _format_group_overrides(self, group_id: str) -> str:
        """格式化按群覆盖配置。"""
        overrides = self.config.overrides
        if not overrides:
            return "（暂无按群覆盖配置）"
        if group_id:
            values = overrides.get(group_id)
            if not values:
                return f"群 {group_id} 暂无覆盖配置。"
            lines = [f"群 {group_id} 覆盖配置："]
            lines.extend(f"• {key} = {value}" for key, value in values.items())
            lines.append(f"使用 /reviewconfig group {group_id} reset 清除全部覆盖。")
            return "\n".join(lines)
        lines = ["按群覆盖配置："]
        for gid, values in overrides.items():
            summary = "，".join(f"{k}={v}" for k, v in values.items())
            lines.append(f"群 {gid}：{summary}")
        return "\n".join(lines)

    def _format_config(self) -> str:
        """格式化当前配置。"""
        config = self.config.all()
        lines = ["⚙️ AI 审核配置："]
        for key, value in config.items():
            if key not in DEFAULT_CONFIG:
                continue
            if isinstance(value, list):
                value = ",".join(str(item) for item in value)
            lines.append(f"• {key} = {value}")
        lines.append(
            "使用 /reviewconfig <key> <value> 修改；"
            "/reviewconfig group <群号> <key> <value> 按群覆盖。"
        )
        return "\n".join(lines)
