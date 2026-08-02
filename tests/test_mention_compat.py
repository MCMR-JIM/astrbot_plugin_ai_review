import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from astrbot.api.message_components import At, AtAll, Plain
except ModuleNotFoundError as exc:
    if not (exc.name == "astrbot" or exc.name.startswith("astrbot.")):
        raise
    ReviewCommandMixin = None
else:
    root = Path(__file__).resolve().parent.parent
    package = types.ModuleType("_plugin_under_test")
    package.__path__ = [str(root)]
    package.__package__ = "_plugin_under_test"
    sys.modules["_plugin_under_test"] = package

    from _plugin_under_test.commands.review import ReviewCommandMixin


@unittest.skipIf(ReviewCommandMixin is None, "AstrBot is not installed")
class MentionCompatibilityTest(unittest.TestCase):
    def test_extract_at_accepts_astrbot_at_component(self):
        event = SimpleNamespace(
            message_obj=SimpleNamespace(
                message=[Plain("/review "), At(qq="10001")]
            )
        )
        self.assertEqual(ReviewCommandMixin._extract_at(event), "10001")

    def test_extract_at_accepts_component_like_at(self):
        event = SimpleNamespace(
            message_obj=SimpleNamespace(
                message=[SimpleNamespace(type="at", qq="10002")]
            )
        )
        self.assertEqual(ReviewCommandMixin._extract_at(event), "10002")

    def test_extract_at_ignores_at_all(self):
        event = SimpleNamespace(
            message_obj=SimpleNamespace(message=[AtAll(qq="all")])
        )
        self.assertEqual(ReviewCommandMixin._extract_at(event), "")

    def test_extract_at_continues_after_at_all(self):
        event = SimpleNamespace(
            message_obj=SimpleNamespace(
                message=[AtAll(qq="all"), At(qq="10003")]
            )
        )
        self.assertEqual(ReviewCommandMixin._extract_at(event), "10003")
