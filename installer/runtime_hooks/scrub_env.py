"""PyInstaller runtime hook for the macOS app: drop the environment variables
that would make a library inside the app load code from outside it.

macOS grants the Accessibility permission to whatever runs as this app, so
code that gets itself loaded here inherits the grant. The hardened runtime
makes dyld ignore DYLD_INSERT_LIBRARIES and friends, but the app must allow
libraries signed by others (its certificate has no Team ID), so a library
that opens a file named in the environment still loads it:

- OpenSSL (hashlib, at launch) reads OPENSSL_CONF, whose provider and engine
  lines load any library, and OPENSSL_MODULES and OPENSSL_ENGINES. Its
  compiled-in config file can sit in a user-writable folder (Homebrew's
  etc/openssl@3, say), so OPENSSL_CONF is pinned to an empty file instead.
- Qt reads QT_QPA_PLATFORM_PLUGIN_PATH, QT_PLUGIN_PATH and more; QML too.
- SSL_CERT_FILE and SSL_CERT_DIR choose which certificates OpenSSL trusts.
- PYTHON* and DYLD_* do nothing in here (a built app ignores the first, and
  the hardened runtime the second), but children would inherit them.
- The programs the app starts (pgrep, codesign, ditto, open, and bash for
  the update swap) also run as the app. They are named by absolute path, and
  PATH is pinned to the system folders anyway, so a program placed earlier
  on the caller's PATH never runs. bash runs with -p, and BASH_ENV, ENV and
  exported functions (BASH_FUNC_*) go too: each makes bash run code it was
  handed, before or instead of the script's own commands.

Custom runtime hooks run before PyInstaller's own (whose PySide6 hook then
points QT_PLUGIN_PATH and QML2_IMPORT_PATH back into the bundle) and before
any app code. ``--self-test`` keeps QT_QPA_PLATFORM, so a check can choose
the Qt platform plugin it loads (the bundled ones only).
"""


def _scrub_environment() -> None:
    import os
    import sys

    prefixes = ("OPENSSL_", "SSL_CERT_", "QT_", "QML", "PYTHON", "DYLD_", "BASH_FUNC_", "__BASH_FUNC")
    shell = {"BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS"}
    keep = {"QT_QPA_PLATFORM"} if "--self-test" in sys.argv[1:] else set()
    for name in list(os.environ):
        if (name.startswith(prefixes) or name in shell) and name not in keep:
            del os.environ[name]  # unsetenv: C libraries no longer see it either
    os.environ["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    os.environ["OPENSSL_CONF"] = os.devnull


_scrub_environment()
del _scrub_environment
