"""Is a full-screen game in front? For pausing the filter while one is
(the "auto_pause_games" setting, off by default).

Games read the mouse in their own way, and some anti-cheat software
distrusts anything that sits in the input path, so a player may want the
filter out of the way while they play. This module only answers the
question; the controller pauses and resumes.

macOS: the same test the system's own Game Mode makes. An Apple engineer
(developer.apple.com/forums/thread/739387): the system decides an app is a
game by its LSApplicationCategoryType, and Game Mode activates when a game is
full screen and the frontmost app. Here: the frontmost app's Info.plist
declares "public.app-category.games" or one of its subcategories
("public.app-category.action-games" and the rest, which all end in
"-games"), or asks for Game Mode itself (LSSupportsGameMode, macOS 26; the
older GCSupportsGameMode), which App Review allows only for games. AND one of
its windows on screen covers a whole display (native full screen, a
borderless full-screen window, or a captured display). Window bounds and
owners come from CGWindowListCopyWindowInfo, which needs no permission (only
window titles do, and they aren't read). Whether Game Mode itself is on has
no public API.

Limits: a game whose bundle declares neither (some ports and launchers
don't), or whose full-screen window belongs to a child process (a Java game
started by a launcher, which Game Mode misses too), isn't seen.

Windows, in order:

* SHQueryUserNotificationState (documented since Vista) answers
  QUNS_RUNNING_D3D_FULL_SCREEN for a full-screen exclusive Direct3D app,
  which is a game in practice;
* otherwise the foreground window must cover its monitor (a borderless
  full-screen window), and either the same call answers QUNS_BUSY ("a
  full-screen application is running", which it also says for Presentation
  Settings: hence the window check), or the program is one Windows itself
  knows as a game: the games Game Bar and Game Mode recognise are listed
  under HKCU\\System\\GameConfigStore\\Children, each with its
  MatchedExeFullPath. That key isn't documented (Lenovo Legion Toolkit,
  Special K and Raycast read it the same way); without it, only the first
  two tests count. Windows has no public API that says whether Game Mode is
  boosting another program (HasExpandedResources answers for the caller
  only), so that isn't asked.

Programs that go full screen without being games never count: browsers
(videos, F11), video players, slide shows, remote desktop and virtual
machine windows (the mouse there is still this computer's), the desktop, the
lock screen and screen savers (NOT_GAMES). A program Windows won't name
(a game whose anti-cheat refuses even the limited query for its path) still
counts when Windows says it is exclusive full screen or busy full screen.

Limits: a game in a browser, or in a program on the NOT_GAMES list, isn't
seen; another full-screen program that isn't a game may be taken for one.
"""

from __future__ import annotations

import logging
import ntpath
import platform
import time
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Any, Optional

log = logging.getLogger(__name__)

#: How often the controller asks while the setting is on.
POLL_INTERVAL_MS = 2000

GAMES_CATEGORY = "public.app-category.games"
CATEGORY_PREFIX = "public.app-category."
#: Info.plist keys that ask for Game Mode (macOS 26, and macOS 14 to 15).
GAME_MODE_KEYS = ("LSSupportsGameMode", "GCSupportsGameMode")

# SHQueryUserNotificationState answers (shellapi.h).
QUNS_NOT_PRESENT = 1
QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4
QUNS_ACCEPTS_NOTIFICATIONS = 5
QUNS_QUIET_TIME = 6
QUNS_APP = 7

#: Where Windows lists the games it recognises (see the module notes).
GAME_CONFIG_STORE = r"System\GameConfigStore\Children"
#: How long that list is kept before it is read again.
GAME_LIST_TTL_S = 60.0

#: Programs that go full screen without being games: browsers (videos, F11),
#: video players, slide shows and meetings, remote and virtual machines, and
#: Windows itself. Executable names, lower case.
NOT_GAMES = frozenset({
    # browsers
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "opera_gx.exe", "vivaldi.exe",
    "iexplore.exe", "arc.exe", "msedgewebview2.exe", "librewolf.exe", "waterfox.exe", "zen.exe",
    # video players
    "vlc.exe", "mpc-hc.exe", "mpc-hc64.exe", "mpc-be.exe", "mpc-be64.exe", "potplayer.exe", "potplayer64.exe",
    "potplayermini.exe", "potplayermini64.exe", "wmplayer.exe", "microsoft.media.player.exe", "video.ui.exe",
    "mpv.exe", "kodi.exe", "plex.exe", "netflix.exe",
    # slides and meetings
    "powerpnt.exe", "pptview.exe", "soffice.bin", "teams.exe", "ms-teams.exe", "zoom.exe",
    # remote desktops and virtual machines: the clicks are still this mouse's
    "mstsc.exe", "msrdc.exe", "msrdcw.exe", "windowsapp.exe", "vmconnect.exe", "vmware.exe", "vmplayer.exe",
    "virtualboxvm.exe", "anydesk.exe", "teamviewer.exe", "rustdesk.exe",
    # Windows itself: the desktop, Store app frames, the lock and sign-in screens
    "explorer.exe", "applicationframehost.exe", "lockapp.exe", "logonui.exe",
})
#: Screen savers run as "<name>.scr".
NOT_GAME_SUFFIXES = (".scr",)

#: What an unnamed game is called (see the module notes).
UNNAMED = ""


@dataclass(frozen=True)
class Game:
    """A full-screen game in front."""

    #: Stable for the same game: its bundle identifier or executable name
    #: ("" for one Windows wouldn't name).
    key: str
    #: What to call it: the app's name, or the executable's without ".exe"
    #: ("" for one Windows wouldn't name).
    name: str


def is_games_category(category: Any) -> bool:
    """Whether an LSApplicationCategoryType value is a games category."""
    if not isinstance(category, str):
        return False
    category = category.strip().lower()
    return category == GAMES_CATEGORY or (category.startswith(CATEGORY_PREFIX) and category.endswith("-games"))


def declares_game(info: Any) -> bool:
    """Whether an app's Info.plist values (a dict) say it is a game: a games
    category, or a request for Game Mode."""
    if not isinstance(info, dict):
        return False
    return is_games_category(info.get("LSApplicationCategoryType")) or any(
        info.get(key) is True for key in GAME_MODE_KEYS
    )


def covers(window: tuple, display: tuple, slack: float = 1.0) -> bool:
    """Whether a window's (x, y, width, height) covers a display's."""
    wx, wy, ww, wh = window
    dx, dy, dw, dh = display
    return (
        wx <= dx + slack
        and wy <= dy + slack
        and wx + ww >= dx + dw - slack
        and wy + wh >= dy + dh - slack
        and dw > 0
        and dh > 0
    )


def not_a_game(exe: str) -> bool:
    return exe in NOT_GAMES or exe.endswith(NOT_GAME_SUFFIXES)


_checkers: dict[str, Any] = {}


def game_in_front() -> Optional[Game]:
    """The full-screen game in front, or None. Never raises."""
    system = platform.system()
    try:
        if system == "Darwin":
            checker = _checkers.get(system) or _checkers.setdefault(system, MacGames())
            return checker.in_front()
        if system == "Windows":
            checker = _checkers.get(system) or _checkers.setdefault(system, WindowsGames())
            return checker.in_front()
    except Exception:  # noqa: BLE001 - unsure: not a game, the filter stays on
        log.warning("Couldn't tell whether a game is in front", exc_info=True)
    return None


# -- macOS ---------------------------------------------------------------------

class MacGames:
    """game_in_front on macOS. `front`, `bundle_info`, `windows` and
    `displays` are what a test replaces."""

    def __init__(self) -> None:
        # Info.plist values by bundle path: an app's Info.plist doesn't
        # change while it runs, and this is asked every couple of seconds.
        self._info: dict[str, dict] = {}

    def front(self) -> Optional[tuple[int, str, str, str]]:
        """(pid, bundle identifier, name, bundle path) of the frontmost app."""
        import AppKit

        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        url = app.bundleURL()
        return (
            int(app.processIdentifier()),
            str(app.bundleIdentifier() or ""),
            str(app.localizedName() or ""),
            str(url.path()) if url is not None else "",
        )

    def bundle_info(self, bundle_path: str) -> dict:
        """The Info.plist values that say whether the app is a game."""
        if not bundle_path:
            return {}
        if bundle_path not in self._info:
            import AppKit

            bundle = AppKit.NSBundle.bundleWithPath_(bundle_path)
            found: dict[str, Any] = {}
            if bundle is not None:
                category = bundle.objectForInfoDictionaryKey_("LSApplicationCategoryType")
                if category is not None:
                    found["LSApplicationCategoryType"] = str(category)
                for key in GAME_MODE_KEYS:
                    value = bundle.objectForInfoDictionaryKey_(key)
                    if value is not None:
                        found[key] = bool(value)
            self._info[bundle_path] = found
        return self._info[bundle_path]

    def windows(self, pid: int) -> list[tuple]:
        """The on-screen windows of process `pid`, as (x, y, width, height)."""
        import Quartz

        options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        found = []
        for info in Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []:
            if int(info.get(Quartz.kCGWindowOwnerPID, -1)) != pid:
                continue
            bounds = info.get(Quartz.kCGWindowBounds) or {}
            found.append((
                float(bounds.get("X", 0)), float(bounds.get("Y", 0)),
                float(bounds.get("Width", 0)), float(bounds.get("Height", 0)),
            ))
        return found

    def displays(self) -> list[tuple]:
        import Quartz

        error, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
        if error:
            return []
        rects = []
        for display in list(ids)[:count]:
            rect = Quartz.CGDisplayBounds(display)
            rects.append((rect.origin.x, rect.origin.y, rect.size.width, rect.size.height))
        return rects

    def in_front(self) -> Optional[Game]:
        front = self.front()
        if front is None:
            return None
        pid, identifier, name, path = front
        if not declares_game(self.bundle_info(path)):
            return None  # the cheap test first: most apps stop here
        displays = self.displays()
        if not any(covers(window, display) for window in self.windows(pid) for display in displays):
            return None
        return Game(identifier or path, name or identifier or "A game")


# -- Windows -------------------------------------------------------------------

class WindowsGames:
    """game_in_front on Windows. `state`, `foreground`, `path_of`,
    `monitor_of` and `known_games` are what a test replaces."""

    def __init__(self, clock=time.monotonic) -> None:
        self._user32 = None
        self._shell32 = None
        self._front = None
        self._clock = clock
        self._known: frozenset = frozenset()
        self._known_at: Optional[float] = None

    def _load(self):
        if self._user32 is None:
            import ctypes
            from ctypes import wintypes

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            user32.GetWindowRect.restype = wintypes.BOOL
            user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
            user32.MonitorFromWindow.restype = wintypes.HMONITOR
            user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
            user32.GetMonitorInfoW.restype = wintypes.BOOL
            shell32 = ctypes.WinDLL("shell32")
            shell32.SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]
            shell32.SHQueryUserNotificationState.restype = ctypes.c_long  # HRESULT
            self._monitor_info = MONITORINFO
            self._shell32 = shell32
            self._user32 = user32
        return self._user32

    def state(self) -> int:
        import ctypes

        self._load()
        value = ctypes.c_int(0)
        if self._shell32.SHQueryUserNotificationState(ctypes.byref(value)) != 0:
            return 0
        return int(value.value)

    def foreground(self) -> Any:
        return self._load().GetForegroundWindow()

    def _foreground_api(self):
        if self._front is None:
            from . import frontmost

            self._front = frontmost._WindowsForeground()
        return self._front

    def _image_path(self, pid: int) -> Optional[str]:
        import ctypes
        from ctypes import wintypes

        api = self._foreground_api()
        if not pid:
            return None
        process = api.kernel32.OpenProcess(api.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not process:
            return None
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not api.kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                return None
            return buffer.value or None
        finally:
            api.kernel32.CloseHandle(process)

    def path_of(self, hwnd: Any) -> Optional[str]:
        """The full path of the program that owns `hwnd`; for a Store app's
        frame, the app inside it (as frontmost does). None when Windows
        won't say."""
        from .frontmost import FRAME_HOST, windows_key

        api = self._foreground_api()
        pid = api.pid_of(hwnd)
        path = self._image_path(pid)
        if path is None or windows_key(path) != FRAME_HOST:
            return path
        for hosted in api.hosted_pids(hwnd, pid):
            hosted_path = self._image_path(hosted)
            if hosted_path and windows_key(hosted_path) != FRAME_HOST:
                return hosted_path
        return None

    def monitor_of(self, hwnd: Any) -> Optional[tuple[tuple, tuple]]:
        """(window rect, monitor rect), each as (x, y, width, height)."""
        import ctypes
        from ctypes import wintypes

        user32 = self._load()
        window = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(window)):
            return None
        monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        info = self._monitor_info()
        info.cbSize = ctypes.sizeof(info)
        if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None

        def box(rect) -> tuple:
            return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)

        return box(window), box(info.rcMonitor)

    def known_games(self) -> frozenset:
        """The programs Windows knows as games (normalised full paths), read
        at most every GAME_LIST_TTL_S."""
        now = self._clock()
        if self._known_at is None or now - self._known_at >= GAME_LIST_TTL_S:
            self._known = frozenset(_normal(path) for path in read_game_config_store())
            self._known_at = now
        return self._known

    def in_front(self) -> Optional[Game]:
        state = self.state()
        hwnd = self.foreground()
        if not hwnd:
            return None
        exclusive = state == QUNS_RUNNING_D3D_FULL_SCREEN
        if not exclusive:
            rects = self.monitor_of(hwnd)
            if rects is None or not covers(*rects):
                return None  # not full screen (Presentation Settings, say)
        path = self.path_of(hwnd)
        if not path:
            # Windows won't name it: an anti-cheat that refuses even the
            # limited query, most likely. Its own word that it is exclusive
            # or busy full screen is all there is.
            return Game(UNNAMED, UNNAMED) if exclusive or state == QUNS_BUSY else None
        exe = PureWindowsPath(path).name.lower()
        if not exe or not_a_game(exe):
            return None
        if exclusive or state == QUNS_BUSY or _normal(path) in self.known_games():
            return Game(exe, PureWindowsPath(path).stem or exe)
        return None


def _normal(path: str) -> str:
    return str(path).strip().strip('"').replace("/", "\\").lower()


def read_game_config_store() -> list[str]:
    """MatchedExeFullPath of each game Windows recognises (see the module
    notes), environment variables expanded. [] when there is no such list."""
    import winreg

    paths: list[str] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, GAME_CONFIG_STORE)
    except OSError:
        return paths
    with root:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            try:
                with winreg.OpenKey(root, name) as child:
                    value, kind = winreg.QueryValueEx(child, "MatchedExeFullPath")
            except OSError:
                continue
            if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(value, str) and value.strip():
                paths.append(ntpath.expandvars(value.strip().strip('"')))
    return paths
