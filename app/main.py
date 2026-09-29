"""Application entry point: one instance, one tray icon, one window."""

from __future__ import annotations

import platform
import sys
from typing import Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenuBar, QMessageBox, QSystemTrayIcon

from . import __version__
from .controller import AppController
from .updater import Updater
from .ui import dock, icons
from .ui import tray as tray_module
from .ui.window import MainWindow

SERVER_NAME = "doubleclick-fixer-single-instance"


def _hand_over_to_running_instance(request: bytes = b"show") -> bool:
    """Hand over to an already-running copy. True if one answered.

    b"show" brings up its window; b"quiet" (a background launch such as a
    login item or an update relaunch) leaves it as it is; b"quit" asks it to
    exit, which the Windows installer uses before replacing or removing it.
    """
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    # Generous: a copy still starting up answers late, and giving up early
    # would start a second copy with a second mouse hook.
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
                parent=self.window,
            )
            self.tray.show()

        self.window.closed_to_tray.connect(self._note_hidden)
        self._told_about_tray = False

        self.server = QLocalServer()
        QLocalServer.removeServer(SERVER_NAME)
        self.server.listen(SERVER_NAME)
        self.server.newConnection.connect(self._on_second_instance)

        hints = self.qt.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda _scheme: self._on_theme_changed())
        # Quitting from anywhere (Dock, app menu, logout) stops the hook cleanly.
        self.qt.aboutToQuit.connect(self.controller.shutdown)
        self.menu_bar = self._build_menu_bar()
        # Opening the app again (Launchpad, Spotlight, Finder, Dock) while it
        # runs from the menu bar shows the window. Installed once the event
        # loop runs, after AppKit has registered its own handler.
        QTimer.singleShot(0, lambda: dock.on_reopen(self.show_window))

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
        window_menu = bar.addMenu("Window")
        close = QAction("Close", window_menu)
        close.setShortcut(QKeySequence.StandardKey.Close)
        close.triggered.connect(self.window.close)
        window_menu.addAction(close)
        return bar

    def _on_theme_changed(self) -> None:
        self.window.apply_look()
        if self.tray is not None:
            self.tray.refresh()  # the Windows tray glyph follows the taskbar

    def check_for_updates(self) -> None:
        self.show_window()
        self.window.show_page("general")
        self.updater.check(user_initiated=True)

    def _about(self) -> None:
        QMessageBox.about(
            self.window,
            "About DoubleClick Fixer",
            f"DoubleClick Fixer {__version__}",
        )

    # -- window ------------------------------------------------------------
    def show_window(self) -> None:
        dock.set_visible(True)
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def show_calibration(self) -> None:
        self.show_window()
        self.window.show_calibration()

    def _note_hidden(self) -> None:
        # The window is closed; the app carries on from the menu bar alone.
        dock.set_visible(False)
        # A one-time hint on Windows, where tray icons hide in the overflow.
        # macOS apps don't announce this; the menu bar icon speaks for itself.
        if self.tray is None or self._told_about_tray or platform.system() == "Darwin":
            return
        self._told_about_tray = True
        where = "the menu bar" if platform.system() == "Darwin" else "the notification area"
        self.tray.showMessage(
            "DoubleClick Fixer is still running",
            f"It keeps filtering from the {where.removeprefix('the ')}.",
            icons.tray_icon(self.controller.active),
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
            # window to be reported in.
            QTimer.singleShot(0, lambda: self.window.request_filter(True, prompt=not minimized))
        elif not self.controller.supported():
            QTimer.singleShot(0, self._warn_unsupported)

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
        if self.window.isVisible():
            self.window.save_geometry()
        self.controller.shutdown()
        if self.tray is not None:
            self.tray.hide()
        self.qt.quit()


def main(argv: Optional[list[str]] = None) -> int:
    arguments = list(sys.argv if argv is None else argv)
    minimized = "--minimized" in arguments

    if "--quit" in arguments:
        # Never starts a copy: it only asks a running one to exit.
        _hand_over_to_running_instance(b"quit")
        return 0

    if hasattr(Qt, "AA_DontShowIconsInMenus"):  # keep menus clean on macOS
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)

    if _hand_over_to_running_instance(b"quiet" if minimized else b"show"):
        return 0

    application = Application(arguments)
    return application.start(minimized)
