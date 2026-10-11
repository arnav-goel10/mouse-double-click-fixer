"""What the interface needs from the controller, checked against the real one.

The UI is built alongside the controller's 1.1 additions and its tests run
on tests/_contract_standin.py until those land. Once AppController has the
contract, the stand-in steps aside and these tests check that the real
controller answers everything the UI calls, in the shapes the UI reads.
"""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import ast
import unittest
from pathlib import Path

try:
    import _ui_support as support
    from _contract_standin import CONTRACT
except ImportError:  # run as tests.<module> from the repository root
    from tests import _ui_support as support
    from tests._contract_standin import CONTRACT

UI = Path(__file__).resolve().parents[1] / "app" / "ui"


def controller_calls() -> set[str]:
    """Every attribute the UI reads off something named `controller`."""
    names = set()
    for path in UI.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Attribute):
                continue
            owner = node.value
            if isinstance(owner, ast.Attribute) and owner.attr == "controller":
                names.add(node.attr)
            elif isinstance(owner, ast.Name) and owner.id == "controller":
                names.add(node.attr)
    return names


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = support.application()

    def setUp(self) -> None:
        support.patch_settings(self)

    def test_the_stand_in_covers_everything_the_ui_calls_beyond_1_0(self) -> None:
        from app.controller import AppController

        # What the controller on this branch lacks is what the stand-in has
        # to supply.
        controller = AppController()
        self.addCleanup(controller.shutdown)
        missing = {name for name in controller_calls() if not hasattr(controller, name)}
        self.assertTrue(missing <= set(CONTRACT), sorted(missing - set(CONTRACT)))

    def test_the_ui_never_reaches_into_the_controller(self) -> None:
        private = {name for name in controller_calls() if name.startswith("_")}
        # 1.0's: a late permission signal is matched to the filter that sent it.
        self.assertEqual(private, {"_filter"})

    def test_the_real_controller_answers_the_contract(self) -> None:
        if support.standing_in():
            self.skipTest("the controller's 1.1 additions haven't landed: the UI's tests use the stand-in")
        controller = support.make_controller(self)
        for name in CONTRACT:
            self.assertTrue(hasattr(controller, name), name)
        self.assertIsInstance(controller.status_sentence(), str)
        self.assertIsInstance(controller.pause_shortcut_text, str)
        health = controller.health()
        for field in ("verdict", "detail", "suggestion"):
            self.assertTrue(hasattr(health, field), field)
        self.assertIsInstance(controller.conflicts(), list)
        for key in ("strength", "auto_pause_games", "pause_shortcut_enabled"):
            self.assertIn(key, controller.settings)
        self.assertIsNone(controller.paused_until)
        self.assertFalse(controller.paused)


if __name__ == "__main__":
    unittest.main()
