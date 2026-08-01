import sys
import types
from pathlib import Path
from types import SimpleNamespace

from astrbot.api.message_components import At, Plain

root = Path(__file__).resolve().parent.parent
package = types.ModuleType("_plugin_under_test")
package.__path__ = [str(root)]
package.__package__ = "_plugin_under_test"
sys.modules["_plugin_under_test"] = package

from _plugin_under_test.commands.review import ReviewCommandMixin


def test_extract_at_accepts_astrbot_at_component():
    event = SimpleNamespace(
        message_obj=SimpleNamespace(message=[Plain("/review "), At(qq="10001")])
    )
    assert ReviewCommandMixin._extract_at(event) == "10001"


def test_extract_at_accepts_component_like_at():
    event = SimpleNamespace(
        message_obj=SimpleNamespace(
            message=[SimpleNamespace(type="at", qq="10002")]
        )
    )
    assert ReviewCommandMixin._extract_at(event) == "10002"
