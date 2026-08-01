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
        lines.append("使用 /reviewconfig <key> <value> 修改。")
        return "\n".join(lines)
