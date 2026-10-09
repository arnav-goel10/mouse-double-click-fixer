"""Application entry point: one instance, one tray icon, one window."""

from __future__ import annotations

import os
import platform
import sys
from functools import lru_cache
from time import monotonic, sleep
from typing import Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenuBar, QMessageBox, QSystemTrayIcon

from . import __version__, diagnostics
from .controller import AppController
from .updater import Updater
from .ui import dock, icons
from .ui import tray as tray_module
from .ui.window import MainWindow

SERVER_NAME = "doubleclick-fixer-single-instance"
#: Taken the moment a copy starts, long before its single-instance channel is
#: listening (the portable Windows exe unpacks itself first, which can take
#: seconds).
LOCK_NAME = "doubleclick-fixer.lock"
#: How long a second launch, or --quit, keeps trying to reach a copy that is
#: still starting up.
HAND_OVER_WAIT_S = 10.0


@lru_cache(maxsize=1)
def _windows_session_and_user() -> tuple[int, str]:
    """This process's Terminal Services session and its user's SID (the
    user's name if the SID can't be read). An elevated copy has the same
    user SID as a normal one, so the two still find each other."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    session = wintypes.DWORD()
    kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    if not kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(session)):
        session.value = 0
    user = ""
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
    ]
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    token = wintypes.HANDLE()
    if advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
        try:
            buffer = ctypes.create_string_buffer(256)
            size = wintypes.DWORD()
            # TokenUser = 1: a TOKEN_USER, whose first field points at the SID.
            if advapi32.GetTokenInformation(token, 1, buffer, ctypes.sizeof(buffer), ctypes.byref(size)):
                sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
                text = wintypes.LPWSTR()
                if advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                    user = text.value or ""
                    kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
        finally:
            kernel32.CloseHandle(token)
    if not user:
        user = os.environ.get("USERNAME", "")
    return session.value, user


def _channel_name() -> str:
    """The single-instance channel's name, which also names the lock.

    On Windows a named pipe is machine-wide: one name for everyone would let
    a second signed-in user's copy find the first user's, fail to reach it
    and fail to listen. So the name carries the session and the user. macOS
    keeps both in the user's own temporary folder already."""
    if platform.system() != "Windows":
        return SERVER_NAME
    try:
        session, user = _windows_session_and_user()
    except Exception:  # noqa: BLE001 - a name per user is still better than none
        session, user = 0, os.environ.get("USERNAME", "")
    user = "".join(character if character.isalnum() or character == "-" else "_" for character in user)
    return f"{SERVER_NAME}-{session}-{user}"


def _channel_names() -> list[str]:
    """Where a running copy may be listening: this session's channel, and on
    Windows the machine-wide name copies before 1.0 used (another user's copy
    there refuses the connection, so only this user's own old copy answers)."""
    name = _channel_name()
    return [name] if name == SERVER_NAME else [name, SERVER_NAME]


def _instance_lock():
    from PySide6.QtCore import QDir, QLockFile

    lock = QLockFile(os.path.join(QDir.tempPath(), f"{LOCK_NAME}-{_channel_name()}"))
    # A copy that crashed leaves its lock behind; Qt sees its process is gone
    # and takes the lock over.
    return lock


def _same_session(kernel32, pid: int, session: int) -> bool:
    from ctypes import byref, wintypes

    theirs = wintypes.DWORD()
    # Unknown counts as another session: never wait on a copy that isn't ours.
    return bool(kernel32.ProcessIdToSessionId(pid, byref(theirs))) and theirs.value == session


def _other_copies_running() -> bool:
    """Whether another process of this executable exists in this session: the
    portable exe still unpacking itself (it does that for a few seconds before
    any of this code runs, so it holds no lock yet), or an older copy that
    takes none. Another signed-in user's copy doesn't count."""
    if not getattr(sys, "frozen", False):
        return False
    mine = {os.getpid(), os.getppid()}  # a one-file exe is a launcher plus the app
    name = os.path.basename(sys.executable).lower()
    try:
        if platform.system() == "Windows":
            import ctypes
            from ctypes import wintypes

            class ProcessEntry(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
            kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
            session = wintypes.DWORD()
            if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
                return False
            snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
            if not snapshot or snapshot == ctypes.c_void_p(-1).value:
                return False
            try:
                entry = ProcessEntry()
                entry.dwSize = ctypes.sizeof(entry)
                more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
                while more:
                    if (
                        entry.szExeFile.lower() == name
                        and entry.th32ProcessID not in mine
                        and _same_session(kernel32, entry.th32ProcessID, session.value)
                    ):
                        return True
                    more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
            finally:
                kernel32.CloseHandle(snapshot)
            return False
        import subprocess

        found = subprocess.run(["/usr/bin/pgrep", "-x", os.path.basename(sys.executable)], capture_output=True, text=True)
        return any(int(pid) not in mine for pid in found.stdout.split())
    except Exception:  # noqa: BLE001 - unsure: behave as before
        return False


def _quit_running_copy() -> None:
    """Ask a running copy to exit and wait until it has. Never starts one."""
    lock = _instance_lock()
    deadline = monotonic() + HAND_OVER_WAIT_S
    asked = False
    while monotonic() < deadline:
        if lock.tryLock(0):
            lock.unlock()
            if _other_copies_running():
                # A copy is still starting (no lock yet), or is too old to
                # take one: keep asking until it listens or goes away.
                asked = _hand_over_to_running_instance(b"quit") or asked
                if not asked:
                    sleep(0.2)
                    continue
            elif not asked:
                _hand_over_to_running_instance(b"quit")  # a copy older than 0.2.11
                return
        elif not _hand_over_to_running_instance(b"quit"):
            sleep(0.2)  # a copy is starting and not listening yet
            continue
        # Asked. Wait for it to finish quitting, so files can be replaced.
        asked = True
        if lock.tryLock(0):
            lock.unlock()
            if not _other_copies_running():
                return
        sleep(0.1)


def _hand_over_to_running_instance(request: bytes = b"show") -> bool:
    """Hand over to an already-running copy. True if one answered.

    b"show" brings up its window; b"quiet" (a background launch such as a
    login item or an update relaunch) leaves it as it is; b"quit" asks it to
    exit, which the Windows installer uses before replacing or removing it.
    """
    if platform.system() == "Windows" and request == b"show":
        # Windows only lets the process the user just started bring a window
        # forward; pass that right on, or the running copy's window opens
        # behind and its taskbar button flashes instead.
        try:
            import ctypes

            ctypes.windll.user32.AllowSetForegroundWindow(ctypes.c_uint32(0xFFFFFFFF).value)  # ASFW_ANY
        except Exception:  # noqa: BLE001 - cosmetic
            pass
    for name in _channel_names():
        socket = QLocalSocket()
        socket.connectToServer(name)
        # Generous: a copy still starting up answers late, and giving up
        # early would start a second copy with a second mouse hook. (A name
        # nobody listens on fails at once.)
        if socket.waitForConnected(1000):
            socket.write(request)
            socket.flush()
            socket.waitForBytesWritten(300)
            socket.disconnectFromServer()
            return True
    return False


class Application:
    def __init__(self, argv: list[str]) -> None:
        self.qt = QApplication(argv)
        self.qt.setApplicationName("DoubleClick Fixer")
        self.qt.setApplicationVersion(__version__)
        self.qt.setOrganizationName("DoubleClick Fixer")
        self.qt.setWindowIcon(icons.app_icon())
        if platform.system() == "Windows":
            # Windows 11's own UI font and body size (14 px).
            body = QFont()
            body.setFamilies(["Segoe UI Variable Text", "Segoe UI"])
            body.setPixelSize(14)
            self.qt.setFont(body)
        # Closing the window leaves the filter running in the tray.
        self.qt.setQuitOnLastWindowClosed(False)

        self.controller = AppController()
        self.updater = Updater(self.controller)
        self.window = MainWindow(self.controller, self.updater)
        self.updater.window_visible = self.window.isVisible
        self.updater.quit_requested.connect(self.quit)
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = tray_module.create(
                self.controller,
                on_open=self.show_window,
                on_calibrate=self.show_calibration,
                on_quit=self.quit,
                on_toggle=self.window.request_filter,
                updater=self.updater,
                on_check_updates=self.check_for_updates,
                on_install_update=self.install_update,
                parent=self.window,
            )
            self.tray.show()

        self.window.closed_to_tray.connect(self._note_hidden)
        # A background update waits while the window is open; closing it is
        # the moment to finish.
        self.window.closed_to_tray.connect(self.updater.apply_if_ready)

        self.server = QLocalServer()
        channel = _channel_name()
        if platform.system() == "Windows":
            # Only this user, elevated or not, may connect: a copy run as
            # administrator still hears a normal launch, or the installer's
            # --quit, from the same user.
            self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        QLocalServer.removeServer(channel)
        if not self.server.listen(channel):
            diagnostics.log.warning(
                "The single-instance channel isn't listening (%s): opening the app again won't "
                "find this copy",
                self.server.errorString(),
            )
        self.server.newConnection.connect(self._on_second_instance)

        hints = self.qt.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda _scheme: self._on_theme_changed())
        # Quitting from anywhere (Dock, app menu, logout) stops the hook cleanly,
        # then the log writes out what is left.
        self.qt.aboutToQuit.connect(self.controller.shutdown)
        self.qt.aboutToQuit.connect(diagnostics.shutdown)
        self.menu_bar = self._build_menu_bar()
        # Opening the app again (Launchpad, Spotlight, Finder, Dock) while it
        # runs from the menu bar shows the window. Installed once the event
        # loop runs, after AppKit has registered its own handler.
        QTimer.singleShot(0, lambda: dock.on_reopen(self.show_window))
        # Sleep and session switches can leave the event taps dead or in the
        # way; the window rebuilds or stops the filter on them.
        dock.observe_system(
            on_wake=self.window.system_woke,
            on_session_active=self.window.session_activated,
            on_session_inactive=self.window.session_resigned,
            on_permission_change=self.window.check_permission_soon,
        )

    def _build_menu_bar(self) -> Optional[QMenuBar]:
        """macOS app menu: About, Settings… (⌘,) and Quit, where users expect them."""
        if platform.system() != "Darwin":
            return None
        bar = QMenuBar()
        menu = bar.addMenu("DoubleClick Fixer")
        about = QAction("About DoubleClick Fixer", menu)
        about.setMenuRole(QAction.MenuRole.AboutRole)
        about.triggered.connect(self._about)
        updates = QAction("Check for Updates…", menu)
        updates.setMenuRole(QAction.MenuRole.ApplicationSpecificRole)
        updates.triggered.connect(self.check_for_updates)
        updates.setVisible(self.updater.supported)
        settings = QAction("Settings…", menu)
        settings.setMenuRole(QAction.MenuRole.PreferencesRole)
        settings.setShortcut(QKeySequence.StandardKey.Preferences)
        settings.triggered.connect(lambda: (self.show_window(), self.window.show_page("general")))
        quit_action = QAction("Quit DoubleClick Fixer", menu)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_action.triggered.connect(self.quit)
        menu.addActions([about, updates, settings, quit_action])
        # The standard Window menu: Minimize (⌘M) and Zoom, as in every Mac app.
        window_menu = bar.addMenu("Window")
        minimize = QAction("Minimize", window_menu)
        minimize.setShortcut(QKeySequence("Ctrl+M"))  # Qt's Ctrl is ⌘ on macOS
        minimize.triggered.connect(self.window.showMinimized)
        zoom = QAction("Zoom", window_menu)
        zoom.triggered.connect(self._zoom)
        close = QAction("Close", window_menu)
        close.setShortcut(QKeySequence.StandardKey.Close)
        close.triggered.connect(self.window.close)
        window_menu.addActions([minimize, zoom])
        window_menu.addSeparator()
        window_menu.addAction(close)
        return bar

    def _zoom(self) -> None:
        if self.window.isMaximized():
            self.window.showNormal()
        else:
            self.window.showMaximized()

    def _on_theme_changed(self) -> None:
        self.window.apply_look()
        if self.tray is not None:
            self.tray.refresh()  # the Windows tray glyph follows the taskbar

    def check_for_updates(self) -> None:
        self.show_window()
        self.window.show_page("general")
        self.updater.check(user_initiated=True)

    def install_update(self) -> None:
        """The menu's "Update to X": install with General on screen, where
        the progress, and any failure, shows."""
        self.show_window()
        self.window.show_page("general")
        self.updater.install()

    def _about(self) -> None:
        QMessageBox.about(
            self.window,
            "About DoubleClick Fixer",
            f"DoubleClick Fixer {__version__}",
        )

    # -- window ------------------------------------------------------------
    def show_window(self) -> None:
        dock.set_visible(True)
        # showNormal() would also undo a maximized window; only a minimized
        # one needs it.
        if self.window.isMinimized():
            self.window.showNormal()
        else:
            self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def show_calibration(self) -> None:
        self.show_window()
        self.window.show_calibration()

    def _note_hidden(self) -> None:
        # The window is closed; the app carries on from the menu bar alone.
        dock.set_visible(False)
        # A one-time hint on Windows, where tray icons hide in the overflow:
        # once ever, not once per sign-in. macOS apps don't announce this;
        # the menu bar icon speaks for itself.
        if self.tray is None or self.controller.tray_hint_shown or platform.system() == "Darwin":
            return
        self.controller.note_tray_hint_shown()
        self.tray.showMessage(
            "DoubleClick Fixer is still running",
            "It keeps filtering from the notification area."
            if self.controller.active
            else "Bounce Filter is off. Turn it on from the icon in the notification area.",
            icons.app_icon(),
            4000,
        )

    def _on_second_instance(self) -> None:
        connection = self.server.nextPendingConnection()
        if connection is None:
            return

        def on_ready() -> None:
            request = bytes(connection.readAll())
            connection.deleteLater()
            if request == b"quit":
                self.quit()
            elif request != b"quiet":
                self.show_window()

        # The request may already be waiting by the time this runs.
        if connection.bytesAvailable():
            on_ready()
        else:
            connection.readyRead.connect(on_ready)

    # -- lifecycle ---------------------------------------------------------
    def start(self, minimized: bool) -> int:
        # Background launches (login, update relaunch) stay in the menu bar or
        # notification area; opening the app yourself always shows the window.
        # Without a tray icon there would be no way back in, so show it anyway.
        if self.tray is None:
            minimized = False
        if not minimized:
            self.show_window()
        else:
            dock.set_visible(False)

        if self.controller.settings["fix_enabled"] and self.controller.supported():
            # Restore the filter after the UI is up, so any failure has a
            # window to be reported in, or, in the background, the menu's
            # status line and a few more tries.
            QTimer.singleShot(0, lambda: self.window.restore_filter(background=minimized))
        elif not self.controller.supported():
            QTimer.singleShot(0, self._warn_unsupported)

        self.updater.note_relaunch(self.controller.take_update_result(__version__))
        self.updater.start()
        from . import install_cleanup

        install_cleanup.start()
        return self.qt.exec()

    def _warn_unsupported(self) -> None:
        QMessageBox.information(
            self.window,
            "Not supported here",
            f"System-wide filtering needs Windows or macOS. On {platform.system()} you can still "
            "use the test pad and calibration to measure your mouse.",
        )

    def quit(self) -> None:
        diagnostics.log.info("Quitting")
        if self.window.isVisible():
            self.window.save_geometry()
        self.controller.shutdown()
        if self.tray is not None and platform.system() != "Darwin":
            # Windows leaves a dead icon in the notification area otherwise.
            # macOS removes the item with the app, and hiding it first would
            # be remembered under its autosave name.
            self.tray.hide()
        self.qt.quit()


def main(argv: Optional[list[str]] = None) -> int:
    arguments = list(sys.argv if argv is None else argv)
    minimized = "--minimized" in arguments

    if "--quit" in arguments:
        # Never starts a copy: it only asks a running one to exit.
        _quit_running_copy()
        return 0

    if hasattr(Qt, "AA_DontShowIconsInMenus"):  # keep menus clean on macOS
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)

    request = b"quiet" if minimized else b"show"
    lock = _instance_lock()
    deadline = monotonic() + HAND_OVER_WAIT_S
    while not lock.tryLock(0):
        # Another copy owns the lock: hand over to it, waiting if it is still
        # starting up, rather than run a second mouse hook beside it.
        if _hand_over_to_running_instance(request) or monotonic() > deadline:
            return 0
        sleep(0.2)

    # A copy older than 0.2.11 runs without the lock; ask it too.
    if _hand_over_to_running_instance(request):
        lock.unlock()
        return 0

    # Only the copy that runs logs: a second launch that hands over, or
    # --quit, would otherwise write to the same file at the same time.
    diagnostics.setup(__version__)
    application = Application(arguments)
    application.instance_lock = lock  # held for as long as the app runs
    return application.start(minimized)
