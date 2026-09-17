"""Optional start-at-login integration for Windows and macOS."""

from __future__ import annotations

import platform
import sys
from pathlib import Path

APP_NAME = "DoubleClickFixer"


def _mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.doubleclickfixer.app.plist"


def _launch_arguments() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--minimized"]
    return [sys.executable, str(Path(__file__).resolve().parents[1] / "run.py"), "--minimized"]


def set_enabled(enabled: bool) -> None:
    if platform.system() == "Windows":
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                arguments = _launch_arguments()
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, " ".join(f'"{argument}"' for argument in arguments))
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return

    if platform.system() == "Darwin":
        plist = _mac_plist_path()
        if enabled:
            plist.parent.mkdir(parents=True, exist_ok=True)
            import plistlib

            plist.write_bytes(
                plistlib.dumps(
                    {
                        "Label": "com.doubleclickfixer.app",
                        "ProgramArguments": _launch_arguments(),
                        "RunAtLoad": True,
                    }
                )
            )
        else:
            plist.unlink(missing_ok=True)
        return

    raise RuntimeError("Start at login is supported on Windows and macOS only.")


def remove() -> None:
    set_enabled(False)
