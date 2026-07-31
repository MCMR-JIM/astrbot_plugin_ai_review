"""配置集中管理。

提供插件默认配置与运行时读写，支持热加载与持久化。
所有模块通过 ConfigManager 读取配置，不直接持有 AstrBotConfig。
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.core.config import AstrBotConfig

DEFAULT_CONFIG: dict[str, Any] = {
    "history_count": 50,
    "review_mode": "both",
    "risk_threshold": 80,
    "review_timeout": 300,
    "cooldown": 300,
    "enable_blacklist": False,
    "enable_history": True,
    "prompt_path": "",
    "whitelist": [],
    "min_msg_len": 2,
    "llm_max_concurrency": 3,
    "mute_duration": 600,
    "admin_qq": [],
    "max_chat_chars": 3000,
    "max_msg_chars": 200,
    "punish_pipeline": {},
}


class ConfigManager:
    """插件配置管理器。

    包装 AstrBotConfig，提供统一的读取与修改入口。
    修改后调用 save_config_async 持久化，其余模块经 get_config 回调即可热加载。
    """

    def __init__(self, config: "AstrBotConfig") -> None:
        """初始化配置管理器。

        Args:
            config: AstrBot 传入的插件配置对象。
        """
        self._config = config
        for key, value in DEFAULT_CONFIG.items():
            if key not in config:
                config[key] = value

    @property
    def raw(self) -> dict:
        """底层配置字典（可被 get_config 回调直接使用）。"""
        return self._config

    def get(self, key: str, default: Any = None) -> Any:
        """读取单个配置项。

        Args:
            key: 配置键。
            default: 缺省值。

        Returns:
            配置值。
        """
        return self._config.get(key, default)

    def all(self) -> dict:
        """返回全部配置的副本。

        Returns:
            配置字典副本。
        """
        return dict(self._config)

    async def set_value(self, key: str, raw_value: str) -> tuple[bool, str]:
        """按默认类型转换并写入配置，然后持久化。

        Args:
            key: 配置键。
            raw_value: 字符串形式的原始值。

        Returns:
            (是否成功, 提示信息)。
        """
        if key not in DEFAULT_CONFIG:
            return False, f"未知配置项：{key}"
        try:
            value = self._convert(DEFAULT_CONFIG[key], raw_value)
        except ValueError:
            return False, f"配置项 {key} 的值类型错误。"
        self._config[key] = value
        try:
            save = getattr(self._config, "save_config_async", None)
            if save is not None:
                await save()
            elif hasattr(self._config, "save_config"):
                self._config.save_config()
        except Exception:
            return True, f"{key} = {value}（内存已生效，持久化失败）"
        return True, f"{key} = {value}"

    @staticmethod
    def _convert(default: Any, raw: str) -> Any:
        """按默认值的类型转换原始字符串。"""
        if isinstance(default, bool):
            return str(raw).strip().lower() in ("true", "1", "yes", "on")
        if isinstance(default, int):
            return int(raw)
        if isinstance(default, float):
            return float(raw)
        if isinstance(default, list):
            return [item.strip() for item in str(raw).split(",") if item.strip()]
        if isinstance(default, dict):
            import json

            value = json.loads(str(raw))
            if not isinstance(value, dict):
                raise ValueError("配置项需要 JSON 对象")
            return value
        return str(raw)
