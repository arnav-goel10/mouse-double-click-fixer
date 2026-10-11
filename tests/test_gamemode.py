"""Telling a full-screen game is in front, and pausing for it (off by default)."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

try:
    import _trust_helpers as helpers
except ImportError:
    from tests import _trust_helpers as helpers

import platform
import unittest
from unittest import mock

from app import gamemode
from app.gamemode import Game


class CategoryTests(unittest.TestCase):
    def test_games_and_every_games_subcategory(self) -> None:
        for category in (
            "public.app-category.games", "public.app-category.action-games", "public.app-category.role-playing-games",
            "public.app-category.simulation-games", "public.app-category.word-games", "Public.App-Category.Games",
        ):
            with self.subTest(category=category):
                self.assertTrue(gamemode.is_games_category(category))

    def test_anything_else_isnt(self) -> None:
        for category in (
            None, 7, "", "public.app-category.developer-tools", "public.app-category.entertainment",
            "public.app-category.video", "com.example.games", "games",
        ):
            with self.subTest(category=category):
                self.assertFalse(gamemode.is_games_category(category))


class CoverTests(unittest.TestCase):
    def test_a_window_covering_the_display(self) -> None:
        self.assertTrue(gamemode.covers((0, 0, 2560, 1440), (0, 0, 2560, 1440)))
        self.assertTrue(gamemode.covers((-1, 0, 2561, 1441), (0, 0, 2560, 1440)))
        self.assertTrue(gamemode.covers((2560, 0, 1920, 1080), (2560, 0, 1920, 1080)), "a second display")

    def test_a_window_that_doesnt(self) -> None:
        self.assertFalse(gamemode.covers((0, 25, 2560, 1415), (0, 0, 2560, 1440)), "under the menu bar")
        self.assertFalse(gamemode.covers((0, 0, 1280, 1440), (0, 0, 2560, 1440)))
        self.assertFalse(gamemode.covers((0, 0, 1920, 1080), (2560, 0, 1920, 1080)), "another display")
        self.assertFalse(gamemode.covers((0, 0, 0, 0), (0, 0, 0, 0)))


class DeclaresGameTests(unittest.TestCase):
    def test_a_games_category_or_a_request_for_game_mode(self) -> None:
        self.assertTrue(gamemode.declares_game({"LSApplicationCategoryType": "public.app-category.games"}))
        self.assertTrue(gamemode.declares_game({"LSSupportsGameMode": True}))
        self.assertTrue(gamemode.declares_game({"GCSupportsGameMode": True}))

    def test_anything_else_isnt(self) -> None:
        for info in (None, {}, {"LSApplicationCategoryType": "public.app-category.video"},
                     {"LSSupportsGameMode": False}, {"GCSupportsGameMode": "yes"}):
            with self.subTest(info=info):
                self.assertFalse(gamemode.declares_game(info))


class FakeMac(gamemode.MacGames):
    def __init__(self, front, category, windows, displays=((0, 0, 2560, 1440),), info=None) -> None:
        super().__init__()
        self._front, self._windows, self._displays = front, windows, displays
        self._fake_info = info if info is not None else (
            {"LSApplicationCategoryType": category} if category is not None else {}
        )
        self.window_reads = 0

    def front(self):
        return self._front

    def bundle_info(self, path):
        return self._fake_info

    def windows(self, pid):
        self.window_reads += 1
        return list(self._windows) if pid == self._front[0] else []

    def displays(self):
        return list(self._displays)


class MacGamesTests(unittest.TestCase):
    FRONT = (501, "com.example.Game", "Example Game", "/Applications/Example Game.app")

    def test_a_full_screen_game(self) -> None:
        found = FakeMac(self.FRONT, "public.app-category.action-games", [(0, 0, 2560, 1440)]).in_front()
        self.assertEqual(found, Game("com.example.Game", "Example Game"))

    def test_a_full_screen_app_asking_for_game_mode(self) -> None:
        fake = FakeMac(self.FRONT, None, [(0, 0, 2560, 1440)], info={"LSSupportsGameMode": True})
        self.assertEqual(fake.in_front(), Game("com.example.Game", "Example Game"))

    def test_a_game_in_a_window_isnt(self) -> None:
        self.assertIsNone(FakeMac(self.FRONT, "public.app-category.games", [(100, 100, 1280, 720)]).in_front())

    def test_a_full_screen_app_that_isnt_a_game(self) -> None:
        fake = FakeMac(self.FRONT, "public.app-category.video", [(0, 0, 2560, 1440)])
        self.assertIsNone(fake.in_front())
        self.assertEqual(fake.window_reads, 0, "no window list for an app that isn't a game")

    def test_no_category(self) -> None:
        self.assertIsNone(FakeMac(self.FRONT, None, [(0, 0, 2560, 1440)]).in_front())

    def test_full_screen_on_the_second_display(self) -> None:
        fake = FakeMac(self.FRONT, "public.app-category.games", [(2560, 0, 1920, 1080)],
                       displays=((0, 0, 2560, 1440), (2560, 0, 1920, 1080)))
        self.assertIsNotNone(fake.in_front())

    def test_nothing_in_front(self) -> None:
        self.assertIsNone(FakeMac(None, None, []).in_front())

    @unittest.skipUnless(platform.system() == "Darwin", "macOS only")
    def test_the_real_calls_answer(self) -> None:
        # Reads the app in front and the window list: no permission, nothing shown.
        games = gamemode.MacGames()
        front = games.front()
        self.assertIsNotNone(front)
        # No active display (asleep, or a closed lid with none attached) is
        # an empty list, which in_front() reads as no game: nothing to cover.
        self.assertIsInstance(games.displays(), list)
        self.assertIsInstance(games.windows(front[0]), list)
        self.assertIsInstance(games.bundle_info(front[3]), dict)
        found = gamemode.game_in_front()
        self.assertTrue(found is None or isinstance(found, Game))

    @unittest.skipUnless(platform.system() == "Darwin", "macOS only")
    def test_a_real_bundles_info_is_read(self) -> None:
        # Chess ships with macOS and declares a games category.
        games = gamemode.MacGames()
        info = games.bundle_info("/System/Applications/Chess.app")
        self.assertTrue(gamemode.declares_game(info), info)
        self.assertFalse(gamemode.declares_game(games.bundle_info("/System/Applications/TextEdit.app")))


FULL = ((0, 0, 1920, 1080), (0, 0, 1920, 1080))
WINDOWED = ((0, 0, 1280, 720), (0, 0, 1920, 1080))


class FakeWindows(gamemode.WindowsGames):
    def __init__(self, state, exe="game.exe", rects=FULL, hwnd=0x10, known=()) -> None:
        super().__init__()
        self._state, self._rects, self._hwnd = state, rects, hwnd
        self._path = None if exe is None else "C:\\Games\\" + exe
        self._fake_known = known
        self.store_reads = 0

    def state(self):
        return self._state

    def foreground(self):
        return self._hwnd

    def path_of(self, hwnd):
        return self._path

    def monitor_of(self, hwnd):
        return self._rects

    def known_games(self):
        self.store_reads += 1
        return frozenset(gamemode._normal(path) for path in self._fake_known)


class WindowsGamesTests(unittest.TestCase):
    def test_exclusive_full_screen_direct3d_is_a_game(self) -> None:
        self.assertEqual(FakeWindows(gamemode.QUNS_RUNNING_D3D_FULL_SCREEN, "cs2.exe").in_front(), Game("cs2.exe", "cs2"))

    def test_a_borderless_full_screen_game(self) -> None:
        self.assertEqual(FakeWindows(gamemode.QUNS_BUSY, "eldenring.exe").in_front(), Game("eldenring.exe", "eldenring"))

    def test_a_game_windows_knows_full_screen_whatever_the_state(self) -> None:
        fake = FakeWindows(gamemode.QUNS_ACCEPTS_NOTIFICATIONS, "Hades.exe", known=["c:/games/HADES.EXE"])
        self.assertEqual(fake.in_front(), Game("hades.exe", "Hades"))
        fake = FakeWindows(gamemode.QUNS_ACCEPTS_NOTIFICATIONS, "Hades.exe", rects=WINDOWED,
                           known=["C:\\Games\\Hades.exe"])
        self.assertIsNone(fake.in_front(), "a known game in a window")
        self.assertEqual(fake.store_reads, 0, "the list isn't read for a window that isn't full screen")

    def test_presentation_settings_without_a_full_screen_window(self) -> None:
        self.assertIsNone(FakeWindows(gamemode.QUNS_BUSY, rects=WINDOWED).in_front())

    def test_programs_that_go_full_screen_without_being_games(self) -> None:
        for exe in ("chrome.exe", "msedge.exe", "firefox.exe", "vlc.exe", "powerpnt.exe", "explorer.exe",
                    "mstsc.exe", "vmconnect.exe", "lockapp.exe", "Bubbles.scr"):
            for state in (gamemode.QUNS_BUSY, gamemode.QUNS_RUNNING_D3D_FULL_SCREEN):
                with self.subTest(exe=exe, state=state):
                    self.assertIsNone(FakeWindows(state, exe, known=["C:\\Games\\" + exe]).in_front())

    def test_every_other_state_is_not_a_game(self) -> None:
        for state in (0, gamemode.QUNS_NOT_PRESENT, gamemode.QUNS_PRESENTATION_MODE, gamemode.QUNS_ACCEPTS_NOTIFICATIONS,
                      gamemode.QUNS_QUIET_TIME, gamemode.QUNS_APP):
            with self.subTest(state=state):
                self.assertIsNone(FakeWindows(state).in_front())

    def test_no_foreground_window(self) -> None:
        self.assertIsNone(FakeWindows(gamemode.QUNS_RUNNING_D3D_FULL_SCREEN, hwnd=0).in_front())

    def test_a_program_windows_wont_name(self) -> None:
        unnamed = Game(gamemode.UNNAMED, gamemode.UNNAMED)
        self.assertEqual(FakeWindows(gamemode.QUNS_RUNNING_D3D_FULL_SCREEN, exe=None).in_front(), unnamed)
        self.assertEqual(FakeWindows(gamemode.QUNS_BUSY, exe=None).in_front(), unnamed)
        self.assertIsNone(FakeWindows(gamemode.QUNS_BUSY, exe=None, rects=WINDOWED).in_front())
        self.assertIsNone(FakeWindows(gamemode.QUNS_ACCEPTS_NOTIFICATIONS, exe=None).in_front())

    def test_the_list_of_games_is_read_once_a_minute(self) -> None:
        clock = helpers.Clock(100.0)
        games = gamemode.WindowsGames(clock=clock)
        with mock.patch.object(gamemode, "read_game_config_store", return_value=["C:\\A.exe"]) as read:
            self.assertEqual(games.known_games(), frozenset({"c:\\a.exe"}))
            games.known_games()
            clock.now += gamemode.GAME_LIST_TTL_S
            games.known_games()
        self.assertEqual(read.call_count, 2)

    @unittest.skipUnless(platform.system() == "Windows", "Windows only")
    def test_the_real_calls_answer(self) -> None:
        games = gamemode.WindowsGames()
        self.assertIn(games.state(), range(0, 8))
        self.assertIsInstance(gamemode.read_game_config_store(), list)
        hwnd = games.foreground()
        if hwnd:
            self.assertIsInstance(games.monitor_of(hwnd), (tuple, type(None)))
            path = games.path_of(hwnd)
            self.assertTrue(path is None or path.lower().endswith(".exe"), path)
        self.assertIsNone(gamemode.game_in_front(), "CI has no game in front")


class GameConfigStoreTests(unittest.TestCase):
    """read_game_config_store against a stand-in for winreg."""

    def test_each_matched_path_with_variables_expanded(self) -> None:
        tree = {
            "a": {"MatchedExeFullPath": ("C:\\Games\\A.exe", 1)},
            "b": {"MatchedExeFullPath": ('"%GAMES%\\B.exe"', 2)},
            "c": {"Other": ("x", 1)},
            "d": {"MatchedExeFullPath": (7, 4)},
        }
        winreg = FakeWinreg(tree)
        with mock.patch.dict("sys.modules", {"winreg": winreg}), mock.patch.dict("os.environ", {"GAMES": "D:\\Steam"}):
            self.assertEqual(gamemode.read_game_config_store(), ["C:\\Games\\A.exe", "D:\\Steam\\B.exe"])

    def test_no_list_is_no_games(self) -> None:
        with mock.patch.dict("sys.modules", {"winreg": FakeWinreg(None)}):
            self.assertEqual(gamemode.read_game_config_store(), [])


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    REG_SZ, REG_EXPAND_SZ = 1, 2

    class Key:
        def __init__(self, values) -> None:
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

    def __init__(self, tree) -> None:
        self.tree = tree

    def OpenKey(self, parent, name):  # noqa: N802 - winreg's names
        if parent == self.HKEY_CURRENT_USER:
            if self.tree is None or name != gamemode.GAME_CONFIG_STORE:
                raise OSError("no such key")
            return self.Key(self.tree)
        return self.Key(parent.values[name])

    def EnumKey(self, key, index):  # noqa: N802
        names = list(key.values)
        if index >= len(names):
            raise OSError("no more")
        return names[index]

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in key.values:
            raise OSError("no such value")
        return key.values[name]


class NeverRaisesTests(unittest.TestCase):
    def test_a_failure_is_no_game(self) -> None:
        with mock.patch.object(gamemode.MacGames, "in_front", side_effect=RuntimeError("x")), \
                mock.patch.object(gamemode.WindowsGames, "in_front", side_effect=OSError("x")):
            self.assertIsNone(gamemode.game_in_front())


class ControllerGameTests(helpers.ControllerCase):
    GAME = Game("com.example.Game", "Example Game")

    def setUp(self) -> None:
        super().setUp()
        self.controller.start_services()
        self.changes = []
        self.controller.pause_changed.connect(lambda: self.changes.append(self.controller.paused))

    def test_off_by_default(self) -> None:
        self.assertFalse(self.controller.settings["auto_pause_games"])
        self.controller.set_active(True)
        self.game = self.GAME
        self.controller.check_game()
        self.assertTrue(self.controller.active)
        self.assertFalse(self.controller._game_timer.isActive(), "no polling while off")

    def test_pauses_while_a_game_is_in_front_and_comes_back_after(self) -> None:
        controller = self.controller
        controller.set_active(True)
        controller.set_auto_pause_games(True)
        self.assertTrue(controller._game_timer.isActive())
        self.game = self.GAME
        controller.check_game()
        self.assertTrue(controller.paused)
        self.assertFalse(controller.active)
        self.assertTrue(controller.wanted)
        self.assertIsNone(controller.paused_until, "no end time")
        self.assertEqual(controller.paused_for_game, "Example Game")
        self.assertEqual(controller.status_sentence(), "Paused while Example Game is in front.")
        self.assertEqual(controller.diagnostic_state()["paused"], "while a full-screen game is in front")
        self.assertEqual(controller.diagnostic_state()["auto-pause in games"], "on")
        controller.check_game()  # still in front: nothing new
        self.assertEqual(self.changes, [True])
        self.game = None
        controller.check_game()
        self.assertFalse(controller.paused)
        self.assertTrue(controller.active)
        self.assertEqual(self.changes, [True, False])

    def test_the_user_resuming_in_a_game_is_respected_until_it_leaves(self) -> None:
        controller = self.controller
        controller.set_active(True)
        controller.set_auto_pause_games(True)
        self.game = self.GAME
        controller.check_game()
        controller.resume()
        self.assertTrue(controller.active)
        controller.check_game()
        self.assertTrue(controller.active, "not paused again for the same game")
        self.game = None
        controller.check_game()
        self.game = self.GAME
        controller.check_game()
        self.assertTrue(controller.paused, "back in front later: paused again")

    def test_turning_the_setting_off_mid_game_filters_again(self) -> None:
        controller = self.controller
        controller.set_active(True)
        controller.set_auto_pause_games(True)
        self.game = self.GAME
        controller.check_game()
        controller.set_auto_pause_games(False)
        self.assertFalse(controller.paused)
        self.assertTrue(controller.active)
        self.assertFalse(controller._game_timer.isActive())

    def test_nothing_happens_while_the_filter_is_off(self) -> None:
        controller = self.controller
        controller.set_auto_pause_games(True)
        self.game = self.GAME
        controller.check_game()
        self.assertFalse(controller.paused)
        self.assertFalse(controller.active)

    def test_a_timed_pause_outlasts_the_game(self) -> None:
        controller = self.controller
        controller.set_active(True)
        controller.set_auto_pause_games(True)
        self.game = self.GAME
        controller.check_game()
        controller.pause(15)
        self.game = None
        controller.check_game()
        self.assertTrue(controller.paused, "the timed pause is still on")
        self.assertFalse(controller.active)
        self.clock.now += 16 * 60
        controller._check_pause_end()
        self.assertTrue(controller.active)

    def test_a_timed_pause_ending_in_a_game_stays_paused_for_the_game(self) -> None:
        controller = self.controller
        controller.set_active(True)
        controller.set_auto_pause_games(True)
        controller.pause(15)
        self.game = self.GAME
        controller.check_game()
        self.clock.now += 16 * 60
        controller._check_pause_end()
        self.assertTrue(controller.paused)
        self.assertEqual(controller.paused_for_game, "Example Game")
        self.assertFalse(controller.active)

    def test_a_game_pause_keeps_test_and_tunes_hold(self) -> None:
        # Test and Tune measuring when a game comes to the front, then the
        # game leaving before the measuring is done: no filter under the pad.
        controller = self.controller
        controller.set_active(True)
        controller.set_auto_pause_games(True)
        controller.suspend()
        self.game = self.GAME
        controller.check_game()
        self.assertTrue(controller.paused)
        self.assertTrue(controller.suspended, "the game's pause is separate")
        self.game = None
        controller.check_game()
        self.assertFalse(controller.paused)
        self.assertFalse(controller.active, "still measuring")
        controller.end_suspension()
        self.assertTrue(controller.active)

    def test_measuring_during_a_game_pause_holds_after_it(self) -> None:
        controller = self.controller
        controller.set_active(True)
        controller.set_auto_pause_games(True)
        self.game = self.GAME
        controller.check_game()
        controller.suspend()
        self.assertTrue(controller.suspended)
        self.game = None
        controller.check_game()
        self.assertFalse(controller.active, "still measuring")
        controller.end_suspension()
        self.assertTrue(controller.active)

    def test_turning_the_filter_off_in_a_game_ends_the_pause(self) -> None:
        controller = self.controller
        controller.set_active(True)
        controller.set_auto_pause_games(True)
        self.game = self.GAME
        controller.check_game()
        controller.set_active(False)
        self.assertFalse(controller.paused)
        controller.check_game()
        self.assertFalse(controller.paused, "off is off")

    def test_a_game_windows_wont_name(self) -> None:
        controller = self.controller
        controller.set_active(True)
        controller.set_auto_pause_games(True)
        self.game = Game(gamemode.UNNAMED, gamemode.UNNAMED)
        controller.check_game()
        self.assertEqual(controller.status_sentence(), "Paused while a full-screen game is in front.")
        self.assertEqual(controller.paused_for_game, "a full-screen game")
        controller.resume()
        controller.check_game()
        self.assertTrue(controller.active, "resumed in it: not paused again while it stays in front")

    def test_another_game_in_front_is_named(self) -> None:
        controller = self.controller
        controller.set_active(True)
        controller.set_auto_pause_games(True)
        self.game = self.GAME
        controller.check_game()
        self.game = Game("other", "Other Game")
        controller.check_game()
        self.assertEqual(controller.paused_for_game, "Other Game")
        self.assertEqual(self.changes, [True, True])

    def test_writing_the_setting_directly_does_the_same(self) -> None:
        # The 1.1 window writes "auto_pause_games" with _apply.
        controller = self.controller
        controller.set_active(True)
        self.game = self.GAME
        controller._apply(auto_pause_games=True)
        self.assertTrue(controller._game_timer.isActive())
        self.assertTrue(controller.paused, "checked at once, not at the next poll")
        controller._apply(auto_pause_games=False)
        self.assertFalse(controller._game_timer.isActive())
        self.assertFalse(controller.paused)
        self.assertTrue(controller.active)

    def test_a_test_controller_never_asks_the_system(self) -> None:
        controller = self.make_controller()
        asked = []
        controller._game_check = lambda: asked.append(1)
        controller.set_active(True)
        controller.set_auto_pause_games(True)
        controller.check_game()
        self.assertEqual(asked, [])

    def test_the_setting_is_saved(self) -> None:
        self.controller.set_auto_pause_games(True)
        from app import settings

        self.assertTrue(settings.load()["auto_pause_games"])

    def test_no_polling_before_the_services_start(self) -> None:
        controller = self.make_controller()
        controller.set_auto_pause_games(True)
        self.assertFalse(controller._game_timer.isActive())


if __name__ == "__main__":
    unittest.main()
