"""Make real HTTPS requests through the TLS backend the app uses, in a
process of its own, and print what each one got as a line of JSON.

tests/test_tls.py's RealHttpsTests runs this on CI. It needs a process of
its own because Qt chooses a TLS backend, and looks for OpenSSL, once per
process: the other tests have done both long before.

    python tests/https_probe.py URL [URL ...]

macOS: a built app runs Qt's OpenSSL backend on the OpenSSL inside it, which
Qt finds because the app's Python modules loaded it as @rpath/libssl.3.dylib
(app/tls.py). From source, Python's ssl module names its libssl by absolute
path instead, which dyld never matches to the bare names Qt asks for, so Qt
would quietly fall back on Secure Transport and the backend that ships would
go untested. So this points Qt at that same file: Qt's own search reads
DYLD_LIBRARY_PATH when it looks for OpenSSL, while dyld read it only as the
process started, so setting it here changes where Qt looks and nothing else.
Only if Python has no OpenSSL library of its own (one linked in statically)
does it use Homebrew's OpenSSL 3 instead.
"""

from __future__ import annotations

import json
import os
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import _isolation  # noqa: F401  (first: keeps this off the real machine)
except ImportError:
    from tests import _isolation  # noqa: F401

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: The only host the CI token is sent to (GitHub limits unauthenticated calls
#: per address, and CI's Macs share theirs).
TOKEN_HOST = "api.github.com"
#: Where Homebrew keeps OpenSSL 3 on Apple silicon and on Intel Macs.
HOMEBREW_OPENSSL = ("/opt/homebrew/opt/openssl@3/lib", "/usr/local/opt/openssl@3/lib")
#: badssl.com's servers now and then reset a connection before the handshake
#: gets anywhere (more often when the client offers a post-quantum key share,
#: as OpenSSL 3.5 and later do), which says nothing about the certificate. A
#: request that ends so, with no HTTP status and no certificate error, is
#: made again, up to this many times in all.
ATTEMPTS = 4
CONNECTION_FAILURES = {
    "RemoteHostClosedError", "ConnectionRefusedError", "TimeoutError", "OperationCanceledError",
    "TemporaryNetworkFailureError", "UnknownNetworkError", "HostNotFoundError",
}


def pythons_libssl() -> str:
    """macOS: the libssl Python's ssl module loaded, or "" if it has none."""
    from app import selftest, tls

    tls.load_bundled_openssl()
    for path in selftest.loaded_images():
        name = os.path.basename(path)
        if name.startswith("libssl.") and not path.startswith(selftest.SYSTEM_LIBRARY_FOLDERS):
            return path
    return ""


def point_qt_at_openssl() -> dict:
    """macOS: make Qt's OpenSSL search find Python's OpenSSL (or Homebrew's)."""
    if sys.platform != "darwin":
        return {}
    libssl = pythons_libssl()
    if libssl:
        folder, source = os.path.dirname(libssl), "Python's"
    else:
        folder = next((folder for folder in HOMEBREW_OPENSSL if os.path.isfile(os.path.join(folder, "libssl.3.dylib"))),
                      "")
        source = "Homebrew's" if folder else "none"
    if folder:
        os.environ["DYLD_LIBRARY_PATH"] = folder
    return {"openssl_source": source, "openssl_folder": folder, "python_libssl": libssl}


def get(manager, url: str, application) -> dict:
    """GET `url`, again while the connection fails before any certificate is
    judged (ATTEMPTS)."""
    failures = []
    for _attempt in range(ATTEMPTS):
        result = get_once(manager, url, application)
        if result["status"] is not None or result["ssl_errors"] or result["error"] not in CONNECTION_FAILURES:
            break
        failures.append(f"{result['error']}: {result['error_string']}")
        time.sleep(2)
    result["earlier_attempts"] = failures
    return result


def get_once(manager, url: str, application) -> dict:
    from PySide6.QtCore import QUrl
    from PySide6.QtNetwork import QNetworkReply, QNetworkRequest

    from app import __version__

    request = QNetworkRequest(QUrl(url))
    request.setRawHeader(b"User-Agent", f"DoubleClickFixer/{__version__} (CI)".encode())
    request.setTransferTimeout(30_000)
    token = os.environ.get("GITHUB_TOKEN", "")
    if token and urlsplit(url).hostname == TOKEN_HOST:
        request.setRawHeader(b"Authorization", f"Bearer {token}".encode())
    reply = manager.get(request)
    ssl_errors: list = []
    reply.sslErrors.connect(lambda errors: ssl_errors.extend(error.error().name for error in errors))
    deadline = time.time() + 60
    while not reply.isFinished() and time.time() < deadline:
        application.processEvents()
        time.sleep(0.01)
    finished = reply.isFinished()
    if not finished:
        reply.abort()
    status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
    body = bytes(reply.readAll()).decode("utf-8", "replace").strip()
    result = {
        "url": url,
        "finished": finished,
        "error": QNetworkReply.NetworkError(reply.error()).name,
        "error_string": reply.errorString(),
        "status": status,
        "ssl_errors": ssl_errors,
        "body": body[:200],
    }
    reply.deleteLater()
    return result


def main(urls) -> int:
    found = point_qt_at_openssl()
    from PySide6.QtNetwork import QNetworkAccessManager, QSslConfiguration, QSslSocket
    from PySide6.QtWidgets import QApplication

    from app import tls

    application = QApplication.instance() or QApplication([])
    backend = tls.use_preferred_backend()
    manager = QNetworkAccessManager()
    try:
        import _ssl

        python_openssl = _ssl.OPENSSL_VERSION
    except ImportError:
        python_openssl = ""
    print(json.dumps({
        "backend": backend,
        "active": QSslSocket.activeBackend(),
        "available": list(QSslSocket.availableBackends()),
        "library": QSslSocket.sslLibraryVersionString(),
        "python_openssl": python_openssl,
        "roots": len(QSslConfiguration.defaultConfiguration().caCertificates()),
        **found,
    }), flush=True)
    for url in urls:
        print(json.dumps(get(manager, url, application)), flush=True)
    if sys.platform == "darwin":
        from app import selftest

        loaded = sorted(path for path in selftest.loaded_images()
                        if selftest.OPENSSL_IMAGE.match(os.path.basename(path)))
        print(json.dumps({"openssl_images": loaded}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
