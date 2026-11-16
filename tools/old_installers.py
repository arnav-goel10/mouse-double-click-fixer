"""The old installers CI upgrades from, kept in a cache and an artifact.

tools/windows_install_e2e.ps1 installs 0.2.6 (before --quit) and 0.5.3 (the
last one-file release) and upgrades each to the new build. Every download of
a release file counts in the release's public download numbers, so CI doesn't
fetch them from the release page on every run: the job restores `old/` from
actions/cache, and

    python tools/old_installers.py fetch old

gets only what the cache lacks. With GITHUB_TOKEN and GITHUB_REPOSITORY set
(as CI sets them), it first looks in the newest `old-installers` artifact an
earlier run uploaded, which counts no release download, and goes to the
release page only for an installer no artifact holds. Caches are kept for a
branch and evicted after a week unused; artifacts are kept for every branch
until they expire. When the newest artifact has less than REFRESH_DAYS left,
or none was found, or anything came from the release page, it writes
`upload=true` to GITHUB_OUTPUT and the job uploads `old/` as a new artifact.

tools/old_installers.json lists each installer's version, URL and SHA-256.
The cache key is a hash of that file, so it names the URLs and checksums:
change either and the cache starts over.

Every installer is checked against its SHA-256 whether it came from the
cache, an artifact or a download, so a cache or an artifact that was tampered
with, or a download that was cut short or swapped, fails the job rather than
being installed. A cached file that doesn't match is fetched again; an
artifact's file that doesn't match is passed over for the release page; a
download that doesn't match fails. Files are laid out as
DIR/<version>/DoubleClickFixer-Setup.exe, the paths the install test is given.

The checksums were read from the releases' own assets (GitHub's `digest`
field, `gh api repos/OWNER/REPO/releases/tags/vX.Y.Z`), which counts no
download.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
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
#: The artifact CI keeps the installers in, and how many days before it
#: expires a run uploads a fresh one.
ARTIFACT = "old-installers"
REFRESH_DAYS = 30
API = "https://api.github.com"


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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Stops at a redirect, so the token never goes to the storage host."""

    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


def api_request(url: str, token: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    })


class ArtifactSource:
    """The newest unexpired `old-installers` artifact of `repository`, read
    through the Actions API with `token`. Its zip is downloaded at most once,
    and only if an installer is missing; any failure on the way leaves the
    release page to supply it."""

    def __init__(self, repository: str, token: str, workdir: Path,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 opener: Optional[urllib.request.OpenerDirector] = None,
                 get: Callable[[str, Path], object] = download,
                 say: Callable[[str], object] = print) -> None:
        self.repository, self.token, self.workdir = repository, token, workdir
        self.now, self.get, self.say = now, get, say
        self.opener = opener or urllib.request.build_opener(_NoRedirect)
        self._listed = False
        self._artifacts: list[dict] = []
        self._zip: Optional[zipfile.ZipFile] = None
        self._failed = False
        self.listing_failed = False

    def artifacts(self) -> list[dict]:
        """Unexpired artifacts named ARTIFACT, newest first; [] if the API
        can't be read."""
        if not self._listed:
            self._listed = True
            url = f"{API}/repos/{self.repository}/actions/artifacts?name={ARTIFACT}&per_page=20"
            try:
                with self.opener.open(api_request(url, self.token), timeout=TIMEOUT_S) as response:
                    listing = json.load(response)
                found = [a for a in listing.get("artifacts", []) if a.get("name") == ARTIFACT and not a.get("expired")]
                self._artifacts = sorted(found, key=lambda a: a.get("created_at", ""), reverse=True)
            except (OSError, ValueError) as error:
                self.say(f"couldn't list the {ARTIFACT} artifacts ({error}); the release page is the fallback")
                self._artifacts = []
                self.listing_failed = True
        return self._artifacts

    def needs_refresh(self) -> bool:
        """True if no artifact will outlast REFRESH_DAYS from now. False if
        the artifacts couldn't be listed: a run that can't read them would
        otherwise upload on every run."""
        artifacts = self.artifacts()
        if self.listing_failed:
            return False
        if not artifacts:
            return True
        try:
            expires = datetime.fromisoformat(str(artifacts[0]["expires_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            return True
        return expires - self.now() < timedelta(days=REFRESH_DAYS)

    def _open_zip(self) -> Optional[zipfile.ZipFile]:
        if self._zip is not None or self._failed:
            return self._zip
        artifacts = self.artifacts()
        if not artifacts:
            self._failed = True
            return None
        newest = artifacts[0]
        target = self.workdir / f"{ARTIFACT}.zip"
        try:
            # GitHub answers with a redirect to a short-lived storage URL,
            # which is fetched without the token.
            try:
                self.opener.open(api_request(newest["archive_download_url"], self.token), timeout=TIMEOUT_S).close()
                raise FetchError("the artifact download didn't redirect")
            except urllib.error.HTTPError as error:
                location = error.headers.get("Location") if error.code in (301, 302, 303, 307, 308) else None
                if not location:
                    raise
            self.get(location, target)
            self._zip = zipfile.ZipFile(target)
            self.say(f"{ARTIFACT}: using the artifact {newest['id']} from {newest.get('created_at', '?')}")
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, FetchError) as error:
            self.say(f"couldn't download the {ARTIFACT} artifact ({error}); the release page is the fallback")
            self._failed = True
        return self._zip

    def __call__(self, installer: Installer, partial: Path) -> bool:
        """Write `installer` from the artifact to `partial`; False if the
        artifact doesn't have it."""
        archive = self._open_zip()
        if archive is None:
            return False
        try:
            with archive.open(f"{installer.version}/{INSTALLER}") as member, partial.open("wb") as out:
                shutil.copyfileobj(member, out, 1 << 20)
        except (KeyError, OSError, zipfile.BadZipFile) as error:
            self.say(f"{installer.version}: not in the {ARTIFACT} artifact ({error})")
            return False
        return True

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()


def fetch(
    folder: Path,
    installers: Sequence[Installer],
    get: Callable[[str, Path], object] = download,
    say: Callable[[str], object] = print,
    source: Optional[Callable[[Installer, Path], bool]] = None,
) -> list[Path]:
    """Leave each installer in `folder`, checked against its SHA-256, and
    return their paths. Only an installer not already there (and right) is
    fetched: from `source` (the artifact) if given and it has the right file,
    otherwise downloaded from its URL."""
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
        try:
            if source is not None and source(installer, partial):
                found = sha256_of(partial)
                if found == installer.sha256:
                    partial.replace(target)
                    say(f"{installer.version}: from the {ARTIFACT} artifact, SHA-256 {installer.sha256} verified; "
                        "nothing downloaded from the release page")
                    continue
                say(f"{installer.version}: the artifact's file has SHA-256 {found}, expected {installer.sha256}")
                partial.unlink(missing_ok=True)
            say(f"{installer.version}: not cached; downloading {installer.url}")
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


def write_output(name: str, value: str) -> None:
    """Set a step output, when run as a GitHub Actions step."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as out:
            out.write(f"{name}={value}\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    fetching = commands.add_parser("fetch", help="fetch what DIR lacks and check every installer's SHA-256")
    fetching.add_argument("folder", type=Path, help="where the installers go, as DIR/<version>/" + INSTALLER)
    arguments = parser.parse_args(argv)
    token, repository = os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", "")
    downloaded: list[str] = []

    def get(url: str, target: Path) -> None:
        downloaded.append(url)
        download(url, target)

    with tempfile.TemporaryDirectory() as workdir:
        source = ArtifactSource(repository, token, Path(workdir)) if token and repository else None
        try:
            fetch(arguments.folder, load(), get=get, source=source)
            if source is not None:
                upload = bool(downloaded) or source.needs_refresh()
                write_output("upload", "true" if upload else "false")
                print(f"{ARTIFACT}: {'a fresh artifact will be uploaded' if upload else 'the artifact is current'}")
        except FetchError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        finally:
            if source is not None:
                source.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
