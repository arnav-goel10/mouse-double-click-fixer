"""Application entry point: one instance, one tray icon, one window."""

from __future__ import annotations

import platform
import sys
from typing import Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from . import __version__
from .controller import AppController
from .ui import icons
from .ui.theme import system_palette
from .ui.tray import Tray
from .ui.window import MainWindow

SERVER_NAME = "doubleclick-fixer-single-instance"


def _hand_over_to_running_instance() -> bool:
    """Ask an already-running copy to show itself. True if one answered."""
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if socket.waitForConnected(300):
        socket.write(b"show")
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
        # Closing the window leaves the filter running in the tray.
        self.qt.setQuitOnLastWindowClosed(False)

        self.controller = AppController()
        self.window = MainWindow(self.controller)
        self.tray: Optional[Tray] = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = Tray(
                self.controller,
                on_open=self.show_window,
                on_calibrate=self.show_calibration,
                on_quit=self.quit,
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
            hints.colorSchemeChanged.connect(lambda _scheme: self.window.apply_palette(system_palette()))

    # -- window ------------------------------------------------------------
    def show_window(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def show_calibration(self) -> None:
        self.show_window()
        self.window.show_calibration()

    def _note_hidden(self) -> None:
        if self.tray is None or self._told_about_tray:
            return
        self._told_about_tray = True
        where = "the menu bar" if platform.system() == "Darwin" else "the notification area"
        self.tray.showMessage(
            "Still running",
            f"DoubleClick Fixer keeps filtering from {where}. Use Quit there to stop it.",
            icons.tray_icon(self.controller.active),
            4000,
        )

    def _on_second_instance(self) -> None:
        connection = self.server.nextPendingConnection()
        if connection is not None:
            connection.readyRead.connect(connection.deleteLater)
        self.show_window()

    # -- lifecycle ---------------------------------------------------------
    def start(self, minimized: bool) -> int:
        if self.controller.settings["start_minimized"] and self.tray is not None:
            minimized = True
        if not minimized:
            self.show_window()

        if self.controller.settings["fix_enabled"] and self.controller.supported():
            # Restore the filter after the UI is up, so any failure has a
            # window to be reported in.
            QTimer.singleShot(0, lambda: self.controller.set_active(True))
        elif not self.controller.supported():
            QTimer.singleShot(0, self._warn_unsupported)

        return self.qt.exec()

    def _warn_unsupported(self) -> None:
        QMessageBox.information(
            self.window,
            "Not supported here",
            f"System-wide filtering needs Windows or macOS. On {platform.system()} you can still "
            "use the test pad and calibration to measure your mouse.",
        )

    def quit(self) -> None:
        self.controller.shutdown()
        if self.tray is not None:
            self.tray.hide()
        self.qt.quit()


def main(argv: Optional[list[str]] = None) -> int:
    arguments = list(sys.argv if argv is None else argv)
    minimized = "--minimized" in arguments

    if hasattr(Qt, "AA_DontShowIconsInMenus"):  # keep menus clean on macOS
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)

    if _hand_over_to_running_instance():
        return 0

    application = Application(arguments)
    return application.start(minimized)
