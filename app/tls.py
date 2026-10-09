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
  Python (its ssl and hashlib modules use the very same files). Qt asks dyld
  for it by bare name first (libcrypto.so.3, libcrypto.3.bundle,
  libcrypto.3.dylib, ...; then libssl), and only if that fails searches
  folders itself: the app's Frameworks folder, then /usr/lib, /usr/local/lib
  and more. In a program with the hardened runtime, as every build is, dyld
  answers a bare name only with a library already loaded as @rpath/<name>,
  from the LC_RPATH folders of Qt Core and the executable (a build has
  none), or from the system (/usr/lib and the OS cryptex), never from the
  current folder. So the app loads its own copies first
  (load_bundled_openssl), which are @rpath/libcrypto.3.dylib and
  @rpath/libssl.3.dylib, and Qt's first try finds exactly those; the
  self-test checks what was loaded (app/selftest.py). If Qt's OpenSSL backend can't start anyway, the app
  uses Secure Transport: macOS's own TLS, deprecated and TLS 1.2 at most,
  which GitHub still accepts, but part of the system rather than a library
  found by searching.
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
