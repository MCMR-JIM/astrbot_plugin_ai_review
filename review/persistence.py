"""KV 持久化适配层。

封装 AstrBot 官方插件 KV 存储（Star 基类提供的 put_kv_data /
get_kv_data），统一为异步 get/put 接口，便于组件注入与测试替换。
写入失败不抛出：降级为内存态并记录警告，避免击穿调用方主流程。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from ..utils.logger import get_logger

KVValue = int | float | str | bytes | bool | dict | list | None

logger = get_logger()


class KVStore:
    """AstrBot 插件 KV 存储的轻量封装。"""

    def __init__(
        self,
        getter: Callable[[str, Any], Awaitable[Any]],
        putter: Callable[[str, KVValue], Awaitable[None]],
    ) -> None:
        """初始化 KV 存储封装。

        Args:
            getter: 读取回调，对应 get_kv_data(key, default)。
            putter: 写入回调，对应 put_kv_data(key, value)。
        """
        self._getter = getter
        self._putter = putter

    async def get(self, key: str, default: Any = None) -> Any:
        """读取键值；读取失败时返回默认值，不抛出。"""
        try:
            return await self._getter(key, default)
        except Exception:
            return default

    async def put(self, key: str, value: KVValue) -> None:
        """写入键值；写入失败仅记录警告，不抛出。"""
        try:
            await self._putter(key, value)
        except Exception as exc:
            logger.warning("[AI审核] KV 写入失败（键 %s，数据保持内存态）：%s", key, exc)
