"""Optional Windows notification-area and macOS menu-bar controller."""

from __future__ import annotations

from typing import Callable, Optional


class TrayController:
    def __init__(self, on_show: Callable[[], None], on_toggle: Callable[[], None], on_remove: Callable[[], None], on_quit: Callable[[], None]) -> None:
        self._on_quit = on_quit
        self._icon = None
        self.started = False
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError as error:
            raise RuntimeError("Install the desktop extras with: pip install -r requirements.txt") from error

        image = Image.new("RGB", (64, 64), "#174b52")
        ImageDraw.Draw(image).ellipse((16, 16, 48, 48), fill="#f9dfc7")
        menu = pystray.Menu(
            pystray.MenuItem("Open DoubleClick Fixer", lambda: on_show()),
            pystray.MenuItem("Enable / disable fix", lambda: on_toggle()),
            pystray.MenuItem("Remove from startup", lambda: on_remove()),
            pystray.MenuItem("Quit", lambda: on_quit()),
        )
        self._icon = pystray.Icon("DoubleClickFixer", image, "DoubleClick Fixer", menu)

    def start(self) -> None:
        if self._icon is not None:
            self._icon.run_detached()
            self.started = True

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.stop()
            self.started = False
