"""KV 持久化适配层。

封装 AstrBot 官方插件 KV 存储（Star 基类提供的 put_kv_data /
get_kv_data），统一为异步 get/put 接口，便于组件注入与测试替换。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

KVValue = int | float | str | bytes | bool | dict | list | None


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
        """写入键值。"""
        await self._putter(key, value)
