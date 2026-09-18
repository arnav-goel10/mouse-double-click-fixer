"""Launch DoubleClick Fixer.

`--minimized` starts it hidden in the menu bar / notification area, which is
what the start-at-login entry uses.
"""

import os
import stat
import sys


def _unhide_qt_plugins() -> None:
    """Make Qt's plugins visible to Qt again when running from source on macOS.

    Some tools (uv, and utilities that hide dot-folders) set the macOS
    "hidden" flag on everything inside a virtualenv. Qt skips hidden files
    when it scans for plugins, so it cannot find its own window-system plugin
    and aborts. Clearing the flag on the plugin folder is harmless and
    touches nothing outside the environment.
    """
    if sys.platform != "darwin" or getattr(sys, "frozen", False):
        return
    try:
        import PySide6
    except ImportError:
        return
    plugins = os.path.join(os.path.dirname(PySide6.__file__), "Qt", "plugins")
    for folder, _directories, files in os.walk(plugins):
        for name in [folder, *(os.path.join(folder, file) for file in files)]:
            try:
                flags = os.lstat(name).st_flags
                if flags & stat.UF_HIDDEN:
                    os.chflags(name, flags & ~stat.UF_HIDDEN)
            except OSError:
                pass


if __name__ == "__main__":
    _unhide_qt_plugins()

    from app.main import main

    sys.exit(main())
