"""app/contracts.py: the stand-ins for INPUT's 1.0 definitions, and the check
that none of them outlives the merge with INPUT's code."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import contracts, core
from app import platform as app_platform

ROOT = Path(__file__).resolve().parents[1]

#: Each name contracts.py offers, and the module INPUT defines it in.
REAL_HOMES = {
    "Button": "core",
    "SIDE_BUTTONS": "core",
    "DEFAULT_THRESHOLD_MS": "core",
    "FilterConfig": "platform",
    "DeviceInfo": "platform",
    "GlobalClickFilter": "platform",
    "WHEEL_DEFAULT_MS": "platform",
}


def input_landmarks(core_module, platform_module, root: Path = ROOT) -> list[str]:
    """Signs that INPUT's 1.0 code is in (design sections 2.1 to 2.3). Any
    one of them will do, so a name INPUT spelled another way than the
    contract can't hide the merge from the check below."""
    found = [f"app/{name}" for name in ("frontmost.py", "devices_mac.py", "devices_win.py") if (root / "app" / name).exists()]
    if hasattr(core_module, "WheelFilter"):
        found.append("core.WheelFilter")
    if hasattr(core_module.Button, "BACK"):
        found.append("core.Button.BACK")
    found += [f"platform.{name}" for name in ("FilterConfig", "DeviceInfo", "WHEEL_DEFAULT_MS") if hasattr(platform_module, name)]
    try:
        first = next(iter(inspect.signature(platform_module.GlobalClickFilter).parameters), "")
    except (TypeError, ValueError, AttributeError):
        first = ""
    if first == "config":
        found.append("GlobalClickFilter(config, ...)")
    return found


def copies_left(contracts_module, core_module, platform_module) -> list[str]:
    """The names contracts.py offers that are not INPUT's own objects."""
    homes = {"core": core_module, "platform": platform_module}
    return [
        name
        for name, home in REAL_HOMES.items()
        if getattr(contracts_module, name) is not getattr(homes[home], name, object())
    ]


class StandInGuardTests(unittest.TestCase):
    def test_no_stand_in_survives_once_input_is_in(self) -> None:
        landmarks = input_landmarks(core, app_platform)
        if not landmarks:
            self.skipTest("INPUT's 1.0 code isn't in yet: contracts.py runs its stand-ins")
        left = copies_left(contracts, core, app_platform)
        self.assertEqual(
            left,
            [],
            f"INPUT's code is in ({', '.join(landmarks)}), but contracts.py still runs its own "
            f"{', '.join(left)}: the window would offer per-button windows, side buttons, the wheel "
            "fix, app exclusions and devices that the filter ignores. Give core and platform the "
            "names in design section 2, or point contracts.py at INPUT's own.",
        )
        self.assertFalse(contracts.STAND_IN)

    def test_the_check_tells_a_stand_in_from_the_real_thing(self) -> None:
        # The modules before INPUT's work: nothing found.
        old_core = SimpleNamespace(Button=SimpleNamespace(LEFT="left"))
        old_platform = SimpleNamespace(GlobalClickFilter=lambda threshold_ms, buttons: None)
        self.assertEqual(input_landmarks(old_core, old_platform, Path("/nonexistent")), [])

        # INPUT's code with FilterConfig under another name: found, and the
        # copies it would leave running are named.
        class NewFilter:
            def __init__(self, config, on_event=None) -> None: ...

        new_core = SimpleNamespace(
            Button=SimpleNamespace(BACK="back"), SIDE_BUTTONS=frozenset(), DEFAULT_THRESHOLD_MS=46, WheelFilter=object
        )
        renamed = SimpleNamespace(GlobalClickFilter=NewFilter, ClickFilterConfig=object)
        self.assertEqual(
            input_landmarks(new_core, renamed, Path("/nonexistent")),
            ["core.WheelFilter", "core.Button.BACK", "GlobalClickFilter(config, ...)"],
        )
        stale = SimpleNamespace(**{name: object() for name in REAL_HOMES})
        self.assertEqual(copies_left(stale, new_core, renamed), list(REAL_HOMES))

        # And once contracts.py hands out INPUT's own objects, nothing is left.
        real_platform = SimpleNamespace(FilterConfig=object, DeviceInfo=object, GlobalClickFilter=NewFilter, WHEEL_DEFAULT_MS=50)
        homes = {"core": new_core, "platform": real_platform}
        merged = SimpleNamespace(**{name: getattr(homes[home], name) for name, home in REAL_HOMES.items()})
        self.assertEqual(copies_left(merged, new_core, real_platform), [])

@unittest.skipIf(contracts.REAL_FILTER, "INPUT's filter is in")
class StandInFilterTests(unittest.TestCase):
    """Until INPUT's filter lands, today's filter runs behind the 1.0 interface."""

    def test_it_takes_the_left_windows_and_the_buttons_it_knows(self) -> None:
        made = []

        class Legacy:
            def __init__(self, threshold_ms, buttons, **callbacks):
                made.append((threshold_ms, buttons, callbacks))
                self.updates = []

            def update(self, threshold_ms=None, buttons=None):
                self.updates.append((threshold_ms, buttons))

            running = True

        config = contracts.FilterConfig(
            thresholds={button: 30 if button is contracts.Button.LEFT else 80 for button in contracts.Button},
            buttons=frozenset({contracts.Button.LEFT, contracts.Button.BACK, contracts.Button.MIDDLE}),
        )
        with mock.patch.object(contracts._platform, "GlobalClickFilter", Legacy):
            hook = contracts.GlobalClickFilter(config, on_event=print, on_device=print, on_wheel=print)
        threshold, buttons, callbacks = made[0]
        self.assertEqual(threshold, 30)
        self.assertEqual(sorted(buttons), [core.Button.LEFT, core.Button.MIDDLE])
        self.assertNotIn("on_device", callbacks)
        self.assertTrue(hook.running, "everything else is the real filter's")
        self.assertEqual(hook.seen_devices(), [])
        hook.update(contracts.FilterConfig(thresholds={**config.thresholds, contracts.Button.LEFT: 40},
                                           buttons=frozenset({contracts.Button.RIGHT})))
        self.assertEqual(hook._inner.updates, [(40, [core.Button.RIGHT])])


if __name__ == "__main__":
    unittest.main()
