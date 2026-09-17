"""Optional start-at-login integration for Windows and macOS."""

from __future__ import annotations

import platform
import sys
from pathlib import Path

APP_NAME = "DoubleClickFixer"
LAUNCH_AGENT_LABEL = "com.doubleclickfixer.app"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def launch_arguments() -> list[str]:
    """The command that starts the app quietly in the background."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--minimized"]
    return [sys.executable, str(Path(__file__).resolve().parents[1] / "run.py"), "--minimized"]


def is_supported() -> bool:
    return platform.system() in ("Windows", "Darwin")


def is_enabled() -> bool:
    system = platform.system()
    if system == "Windows":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
                winreg.QueryValueEx(key, APP_NAME)
                return True
        except OSError:
            return False
    if system == "Darwin":
        return _launch_agent_path().exists()
    return False


def set_enabled(enabled: bool) -> None:
    system = platform.system()
    if system == "Windows":
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                command = " ".join(f'"{argument}"' for argument in launch_arguments())
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return

    if system == "Darwin":
        import plistlib

        plist = _launch_agent_path()
        if enabled:
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_bytes(
                plistlib.dumps(
                    {
                        "Label": LAUNCH_AGENT_LABEL,
                        "ProgramArguments": launch_arguments(),
                        "RunAtLoad": True,
                        "ProcessType": "Interactive",
                    }
                )
            )
        else:
            plist.unlink(missing_ok=True)
        return

    raise RuntimeError("Start at login is supported on Windows and macOS only.")


def remove() -> None:
    if is_supported():
        set_enabled(False)
