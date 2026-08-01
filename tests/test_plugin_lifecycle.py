"""Lifecycle tests that require a real AstrBot installation."""

from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_PKG = "_plugin_under_test"
if _PKG not in sys.modules:
    package = types.ModuleType(_PKG)
    package.__path__ = [str(_REPO_ROOT)]
    package.__package__ = _PKG
    sys.modules[_PKG] = package

try:
    from _plugin_under_test.main import AiReviewPlugin
except ModuleNotFoundError as exc:
    if exc.name != "astrbot":
        raise
    AiReviewPlugin = None


@unittest.skipIf(AiReviewPlugin is None, "real AstrBot is not installed")
class PluginLifecycleTest(unittest.TestCase):
    def test_terminate_cancels_and_awaits_managed_tasks(self) -> None:
        async def scenario() -> None:
            plugin = object.__new__(AiReviewPlugin)
            plugin._bg_tasks = set()
            started = asyncio.Event()

            async def worker() -> None:
                started.set()
                await asyncio.Event().wait()

            task = asyncio.create_task(worker())
            plugin._bg_tasks.add(task)
            await started.wait()

            await plugin.terminate()

            self.assertTrue(task.done())
            self.assertTrue(task.cancelled())

        asyncio.run(scenario())

    def test_terminate_accepts_completed_managed_tasks(self) -> None:
        async def scenario() -> None:
            plugin = object.__new__(AiReviewPlugin)

            async def worker() -> None:
                return None

            task = asyncio.create_task(worker())
            await task
            plugin._bg_tasks = {task}

            await plugin.terminate()

            self.assertTrue(task.done())
            self.assertFalse(task.cancelled())

        asyncio.run(scenario())

    def test_terminate_accepts_no_managed_tasks(self) -> None:
        async def scenario() -> None:
            plugin = object.__new__(AiReviewPlugin)
            plugin._bg_tasks = set()

            await plugin.terminate()

        asyncio.run(scenario())

    def test_terminate_can_be_called_repeatedly(self) -> None:
        async def scenario() -> None:
            plugin = object.__new__(AiReviewPlugin)
            started = asyncio.Event()

            async def worker() -> None:
                started.set()
                await asyncio.Event().wait()

            task = asyncio.create_task(worker())
            plugin._bg_tasks = {task}
            await started.wait()

            await plugin.terminate()
            await plugin.terminate()

            self.assertTrue(task.cancelled())

        asyncio.run(scenario())

    def test_terminate_contains_managed_task_exceptions(self) -> None:
        async def scenario() -> None:
            plugin = object.__new__(AiReviewPlugin)
            plugin._bg_tasks = set()
            active_started = asyncio.Event()

            async def fail() -> None:
                raise RuntimeError("managed task failed")

            async def active() -> None:
                active_started.set()
                await asyncio.Event().wait()

            failed_task = asyncio.create_task(fail())
            active_task = asyncio.create_task(active())
            plugin._bg_tasks.update((failed_task, active_task))
            await active_started.wait()
            await asyncio.sleep(0)
            self.assertTrue(failed_task.done())

            await plugin.terminate()

            self.assertIsInstance(failed_task.exception(), RuntimeError)
            self.assertTrue(active_task.cancelled())

        asyncio.run(scenario())

    def test_terminate_cleans_up_tasks_created_by_spawn(self) -> None:
        async def scenario() -> None:
            plugin = object.__new__(AiReviewPlugin)
            plugin._bg_tasks = set()
            started = asyncio.Event()

            async def worker() -> None:
                started.set()
                await asyncio.Event().wait()

            task = plugin._spawn(worker())
            await started.wait()

            await plugin.terminate()

            self.assertTrue(task.done())
            self.assertTrue(task.cancelled())
            self.assertEqual(plugin._bg_tasks, set())

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
