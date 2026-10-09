"""Which TLS library the app's HTTPS goes through: the update check and its
downloads, the only connections it makes.

Qt Network makes TLS connections through a backend plugin, and left to
itself it prefers OpenSSL whenever it can load it, from files it finds by
name. So the app chooses, before its first request, and never lets Qt pick:

* Windows: Schannel, Windows' own TLS. Qt's OpenSSL backend would load any
  libssl-3-x64.dll in a folder Windows searches, PATH among them, so builds
  leave that plugin and those files out (tools/make_notices.py's
  unused_qt_file, which the spec reads). Without Schannel the update check
  fails and says so: there is no other library it may use.
* macOS: Qt's OpenSSL backend, on the OpenSSL that ships inside the app for
  Python (its ssl and hashlib modules use the very same files). Qt first
  asks dyld for each library by bare name: libcrypto.so.3, then
  libcrypto.3.bundle, then libcrypto.3.dylib (and the same for libssl).
  dyld answers a bare name with a library already loaded as @rpath/<name>,
  or from the LC_RPATH folders of the program (a build's has none) and of
  Qt Core (@loader_path/../../../../../.., which is the app's
  Contents/Frameworks), or from the OS cryptex and /usr/lib. An ordinary
  program would also take it from the current folder, so a libcrypto.so.3
  there would load before the app's copy is ever reached. What prevents
  that is the hardened runtime every build is signed with: dyld then
  refuses relative paths, which holds while System Integrity Protection is
  on (tools/macos_injection_check.sh's cwd leg checks it). Loading the
  app's own copies first (load_bundled_openssl, as @rpath/libcrypto.3.dylib
  and @rpath/libssl.3.dylib) is a correctness aid on top: Qt then binds to
  the very files Python uses, which the self-test checks (app/selftest.py).
  Only if no bare name loads does Qt search folders itself, by absolute
  path: the app's Frameworks folder, then /lib, /usr/lib, /usr/local/lib
  and more. If Qt's OpenSSL backend can't start at all, the app uses
  Secure Transport: macOS's own TLS, deprecated and TLS 1.2 at most, which
  GitHub still accepts, but part of the system rather than a library found
  by searching. A build's self-test fails on Secure Transport, and on any
  OpenSSL loaded from outside the app.

Qt's OpenSSL backend doesn't check whether a certificate has been revoked;
an update installs only with a valid Ed25519 signature regardless
(app/update_signature.py).
"""

from __future__ import annotations

import logging
import sys
from typing import Dict, Iterable, Optional, Tuple

log = logging.getLogger(__name__)

#: Qt's TLS backends the app may use, best first.
PREFERRED: Dict[str, Tuple[str, ...]] = {
    "win32": ("schannel",),
    "darwin": ("openssl", "securetransport"),
}
#: Anywhere else (running from source on Linux).
OTHERWISE: Tuple[str, ...] = ("openssl",)

_chosen: Optional[str] = None


class Unavailable(RuntimeError):
    """None of the TLS backends this platform may use is available."""


def preferred_backends(platform: str = sys.platform) -> Tuple[str, ...]:
    return PREFERRED.get(platform, OTHERWISE)


def choose_backend(available: Iterable[str], platform: str = sys.platform) -> str:
    """The first of this platform's backends among `available`."""
    available = list(available)
    preferred = preferred_backends(platform)
    for name in preferred:
        if name in available:
            return name
    raise Unavailable(f"Qt has no {' or '.join(preferred)} TLS backend here, only {', '.join(available) or 'none'}")


def select_backend(sockets, platform: str = sys.platform) -> str:
    """Make Qt use this platform's backend; `sockets` is QSslSocket (or a
    stand-in with its static methods). Returns the backend's name."""
    name = choose_backend(sockets.availableBackends(), platform)
    if name != preferred_backends(platform)[0]:
        log.warning("Qt's %s TLS backend isn't available; using %s", preferred_backends(platform)[0], name)
    # Qt keeps the first backend it uses; setActiveBackend can't change it.
    if not sockets.setActiveBackend(name) or sockets.activeBackend() != name:
        raise Unavailable(f"Qt is already using its {sockets.activeBackend()} TLS backend, not {name}")
    log.info("TLS through Qt's %s backend (%s)", name, sockets.sslLibraryVersionString())
    return name


def use_preferred_backend() -> str:
    """Choose Qt's TLS backend, once per process, before its first TLS
    connection. Returns its name; raises Unavailable."""
    global _chosen
    if _chosen is None:
        load_bundled_openssl()
        from PySide6.QtNetwork import QSslSocket

        _chosen = select_backend(QSslSocket)
    return _chosen


def load_bundled_openssl() -> None:
    """macOS: load the OpenSSL Python uses before Qt's OpenSSL backend looks
    for one. In a built app those are the copies inside it, loaded as
    @rpath/libcrypto.3.dylib and @rpath/libssl.3.dylib, which is how dyld
    then answers Qt's "libcrypto.3.dylib" and "libssl.3.dylib"."""
    if sys.platform != "darwin":
        return
    try:
        import _hashlib  # noqa: F401  (libcrypto)
        import _ssl  # noqa: F401  (libssl)
    except ImportError as error:
        log.warning("Python's OpenSSL modules didn't load: %s", error)
