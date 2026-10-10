"""The old installers CI upgrades from, downloaded once and kept in a cache.

tools/windows_install_e2e.ps1 installs 0.2.6 (before --quit) and 0.5.3 (the
last one-file release) and upgrades each to the new build. Every download of
a release file counts in the release's public download numbers, so CI doesn't
fetch them on every run: the job restores `old/` from actions/cache, and

    python tools/old_installers.py fetch old

downloads only what the cache lacks. tools/old_installers.json lists each
installer's version, URL and SHA-256. The cache key is a hash of that file,
so it names the URLs and checksums: change either and the cache starts over.

Every installer is checked against its SHA-256 whether it came from the cache
or from a download, so a cache that was tampered with, or a download that was
cut short or swapped, fails the job rather than being installed. A cached
file that doesn't match is downloaded again; a download that doesn't match
fails. Files are laid out as DIR/<version>/DoubleClickFixer-Setup.exe, the
paths the install test is given.

The checksums were read from the releases' own assets (GitHub's `digest`
field, `gh api repos/OWNER/REPO/releases/tags/vX.Y.Z`), which counts no
download.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable, NamedTuple, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools" / "old_installers.json"
INSTALLER = "DoubleClickFixer-Setup.exe"
#: Tries per download, and the seconds to wait before each retry.
ATTEMPTS = 3
RETRY_WAIT_S = 5.0
TIMEOUT_S = 120.0
USER_AGENT = "mouse-double-click-fixer-ci"


class FetchError(Exception):
    """An installer couldn't be had, or isn't the one the manifest names."""


class Installer(NamedTuple):
    version: str
    url: str
    sha256: str


def load(manifest: Path = MANIFEST) -> list[Installer]:
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    installers = []
    for entry in entries:
        installer = Installer(str(entry["version"]), str(entry["url"]), str(entry["sha256"]))
        if not installer.url.startswith("https://"):
            raise FetchError(f"{installer.version}: {installer.url} is not an HTTPS URL")
        if len(installer.sha256) != 64 or any(char not in "0123456789abcdef" for char in installer.sha256):
            raise FetchError(f"{installer.version}: {installer.sha256!r} is not a lower-case SHA-256")
        installers.append(installer)
    if not installers:
        raise FetchError(f"{manifest} lists no installers")
    return installers


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, target: Path, wait: Callable[[float], object] = time.sleep) -> None:
    """Write the body of `url` to `target`, trying up to ATTEMPTS times."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response, target.open("wb") as out:
                for chunk in iter(lambda: response.read(1 << 20), b""):
                    out.write(chunk)
            return
        except OSError as error:  # URLError, HTTPError, timeouts and a full disk alike
            if attempt == ATTEMPTS:
                raise FetchError(f"couldn't download {url}: {error}") from error
            print(f"download of {url} failed ({error}); trying again", flush=True)
            wait(RETRY_WAIT_S)


def fetch(
    folder: Path,
    installers: Sequence[Installer],
    get: Callable[[str, Path], object] = download,
    say: Callable[[str], object] = print,
) -> list[Path]:
    """Leave each installer in `folder`, checked against its SHA-256, and
    return their paths. Only an installer not already there (and right) is
    downloaded."""
    paths = []
    for installer in installers:
        target = folder / installer.version / INSTALLER
        paths.append(target)
        if target.is_file():
            if sha256_of(target) == installer.sha256:
                say(f"{installer.version}: cached, SHA-256 {installer.sha256} verified; nothing downloaded")
                continue
            say(f"{installer.version}: the cached file is not the one listed; downloading it again")
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        for leftover in (target, partial):
            leftover.unlink(missing_ok=True)
        say(f"{installer.version}: not cached; downloading {installer.url}")
        try:
            get(installer.url, partial)
            found = sha256_of(partial)
            if found != installer.sha256:
                raise FetchError(f"{installer.version}: {installer.url} has SHA-256 {found}, expected {installer.sha256}")
            partial.replace(target)
        finally:
            # Nothing unchecked is left where a cache would pick it up.
            partial.unlink(missing_ok=True)
        say(f"{installer.version}: downloaded, SHA-256 {installer.sha256} verified")
    return paths


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    fetching = commands.add_parser("fetch", help="download what DIR lacks and check every installer's SHA-256")
    fetching.add_argument("folder", type=Path, help="where the installers go, as DIR/<version>/" + INSTALLER)
    arguments = parser.parse_args(argv)
    try:
        fetch(arguments.folder, load())
    except FetchError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
