"""Optional start-at-login integration for Windows and macOS."""

from __future__ import annotations

import platform
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

APP_NAME = "DoubleClickFixer"
LAUNCH_AGENT_LABEL = "com.doubleclickfixer.app"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
#: Where Task Manager and Settings > Apps > Startup record that the user
#: turned an entry off. They leave the Run value in place.
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
LOGIN_ITEMS_PANE = "x-apple.systempreferences:com.apple.LoginItems-Settings.extension"

#: What status() reports.
ON = "on"
OFF = "off"
#: Set to open at login, but the user switched it off in System Settings >
#: General > Login Items, or in Task Manager's Startup apps.
BLOCKED = "blocked"

# SMAppServiceStatus (ServiceManagement, macOS 13+).
_SM_REQUIRES_APPROVAL = 2


def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def launch_arguments() -> list[str]:
    """The command that starts the app quietly in the background."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--minimized"]
    return [sys.executable, str(Path(__file__).resolve().parents[1] / "run.py"), "--minimized"]


def is_supported() -> bool:
    return platform.system() in ("Windows", "Darwin")


def running_from_temporary_location() -> bool:
    """Whether this copy runs from somewhere that won't last: straight from
    the disk image (/Volumes), or from the random read-only folder macOS
    moves a downloaded app to until it is moved itself (App Translocation).
    A login item pointing there finds nothing after an eject or a reboot."""
    path = sys.executable
    return path.startswith("/Volumes/") or "/AppTranslocation/" in path


def status() -> str:
    """ON, OFF, or BLOCKED: whether the app will open at login, read from the
    system each time, because the user can change it there at any moment."""
    system = platform.system()
    if system == "Windows":
        if not _run_value_exists():
            return OFF
        return BLOCKED if _turned_off_in_task_manager() else ON
    if system == "Darwin":
        plist = _launch_agent_path()
        if not plist.exists():
            return OFF
        return BLOCKED if _legacy_status(plist) == _SM_REQUIRES_APPROVAL else ON
    return OFF


def is_enabled() -> bool:
    return status() == ON


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
        # Turning it on here is the user's answer to an earlier "off" in Task
        # Manager, which would otherwise outlive the Run value being rewritten.
        _clear_task_manager_choice()
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
                        # System Settings › Login Items then lists it under the
                        # app's own name and icon, not as an unknown item.
                        "AssociatedBundleIdentifiers": [LAUNCH_AGENT_LABEL],
                    }
                )
            )
        else:
            plist.unlink(missing_ok=True)
        return

    raise RuntimeError("Start at login is supported on Windows and macOS only.")


def open_login_items_settings() -> None:
    """macOS: open System Settings > General > Login Items, the only place a
    login item the user switched off can be switched back on."""
    if platform.system() != "Darwin":
        return
    service = _app_service()
    if service is not None:
        try:
            service.openSystemSettingsLoginItems()
            return
        except Exception:  # noqa: BLE001 - fall back to the pane's URL
            pass
    subprocess.Popen(["/usr/bin/open", LOGIN_ITEMS_PANE])


def remove() -> None:
    if is_supported():
        set_enabled(False)


# -- Windows -------------------------------------------------------------------

def _run_value_exists() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def _turned_off_in_task_manager() -> bool:
    """Explorer skips a Run entry whose StartupApproved value starts with an
    odd byte (03 for disabled; 02 is enabled). No value means enabled."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY, 0, winreg.KEY_READ) as key:
            data, _kind = winreg.QueryValueEx(key, APP_NAME)
    except OSError:
        return False
    return isinstance(data, (bytes, bytearray)) and len(data) > 0 and data[0] % 2 == 1


def _clear_task_manager_choice() -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
    except OSError:
        pass  # no key, or no value: nothing was turned off


# -- macOS ---------------------------------------------------------------------

@lru_cache(maxsize=1)
def _app_service():
    """ServiceManagement's SMAppService class (macOS 13+), loaded from the
    system framework, or None."""
    try:
        import objc

        objc.loadBundle(
            "ServiceManagement", {}, bundle_path="/System/Library/Frameworks/ServiceManagement.framework"
        )
        return objc.lookUpClass("SMAppService")
    except Exception:  # noqa: BLE001 - older macOS, or no PyObjC
        return None


def _legacy_status(plist: Path) -> Optional[int]:
    """What macOS's Login Items list says about a LaunchAgent plist:
    1 enabled, 2 switched off by the user (it "requires approval"), 0 or 3
    not known (yet). None when it can't be asked."""
    service = _app_service()
    if service is None:
        return None
    try:
        from Foundation import NSURL

        return int(service.statusForLegacyURL_(NSURL.fileURLWithPath_(str(plist))))
    except Exception:  # noqa: BLE001 - unknown: trust the file
        return None
