"""Updates from GitHub Releases.

The updater asks GitHub for the latest release and downloads its
SHA256SUMS.txt with that file's minisign signature. The signature must come
from one of the release keys below, and its signed comment must name the
release's version (app/update_signature.py has the format,
tools/sign_release.py makes it); a release that fails this is never offered.
Installing it downloads the file for this platform, checks it against the
signed checksums, and installs it:

* macOS: a zipped app bundle. It must carry a valid code signature whose
  designated requirement matches the running app's. That is what macOS keys
  the Accessibility grant on, so an update that passes this check keeps the
  permission. A release that moves to a new signing identity says so in its
  signed comment. The bundle must also say it is the release's version.
* Windows (installed): the Inno Setup installer, run silently; it upgrades in
  place and relaunches the app.
* Windows (portable): the executable itself, swapped once the app has quit.

The swap happens in a small detached script after the app exits, since a
running program cannot replace its own files.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import __version__
from .update_signature import PublicKey, ReleaseClaim, SignatureError, parse_claim, parse_public_key, verify

log = logging.getLogger(__name__)

REPOSITORY = "arnav-goel10/doubleclick-fixer"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
#: Points the updater at another server; used by the end-to-end test.
URL_OVERRIDE_ENV = "DCF_UPDATE_URL"

MAC_ASSET = "DoubleClickFixer-macos.zip"
WINDOWS_INSTALLER_ASSET = "DoubleClickFixer-Setup.exe"
WINDOWS_PORTABLE_ASSET = "DoubleClickFixer.exe"
CHECKSUM_ASSET = "SHA256SUMS.txt"
SIGNATURE_ASSET = CHECKSUM_ASSET + ".minisig"

#: The minisign public keys a release must be signed with (the same keys are
#: in tools/keys/). The owner keeps the secret keys offline. The backup key is
#: built in from the start, so a lost or retired primary key can be replaced
#: without stranding every installed copy.
RELEASE_KEYS = (
    "RWR9XcCRL9bZ1OD22V5J6uVhJgblDA9o4UFwJBjMU6CtTyqCeWOC6jDf",  # primary, D4D9D62F91C05D7D
    "RWTUZv3t2LmICpA6R0C6kmRCHkbU8DUvmw5kDkjIXZ1yJnUNuTXlEq7Z",  # backup, 0A88B9D8EDFD66D4
)
#: A throwaway public key that tests sign their stand-in releases with. Only a
#: copy running from source reads it: a packaged app never takes a key from its
#: environment, which whatever starts the app controls.
TEST_KEY_ENV = "DCF_UPDATE_TEST_KEY"

#: The Windows update scripts find the running app and the download through
#: these. cmd reads a batch file in the OEM code page, which can't spell every
#: path, but expands variables from its Unicode environment intact.
APP_ENV = "DCF_APP"
SOURCE_ENV = "DCF_SRC"

CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000
FIRST_CHECK_DELAY_MS = 20 * 1000
#: A request that transfers nothing for this long is abandoned, so a stalled
#: connection ends in "Try Again" rather than a spinner that never stops.
STALL_TIMEOUT_MS = 30 * 1000
WORKDIR_PREFIX = "dcf-update-"
#: A version that failed to install this many times is no longer installed
#: unattended, only when the user asks. Retrying it would quit the app again at
#: every sign-in.
GIVE_UP_AFTER = 2
#: The checksums and their signature are a few hundred bytes; a download much
#: bigger than this isn't them, and is stopped.
SMALL_FILE_LIMIT = 64 * 1024

UNSIGNED = "This update isn’t signed, so it can’t be installed."
MOVE_TO_APPLICATIONS = "Move DoubleClick Fixer to Applications to update it."
CHECK_FAILED = "Couldn’t check for updates."
INSTALL_FAILED = "The update didn’t install. Try again."


# -- pure helpers (unit tested) ---------------------------------------------------

def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' -> (1, 2, 3). Anything after the numbers is ignored."""
    match = re.match(r"^\s*v?(\d+(?:\.\d+)*)", text or "")
    if not match:
        return (0,)
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def parse_checksums(text: str) -> dict[str, str]:
    """Read `sha256sum` output: '<hex>  <name>' per line."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            result[parts[-1].lstrip("*")] = parts[0].lower()
    return result


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class Release:
    version: str
    page_url: str
    asset_name: str
    asset_url: str
    checksum_url: str
    #: Empty when the release has no signature; it is then never offered.
    signature_url: str = ""


def installation_kind() -> str:
    """'mac', 'windows-installed', 'windows-portable', or 'source'."""
    if not getattr(sys, "frozen", False):
        return "source"
    if platform.system() == "Darwin":
        return "mac"
    if platform.system() == "Windows":
        folder = Path(sys.executable).parent
        return "windows-installed" if any(folder.glob("unins*.exe")) else "windows-portable"
    return "source"


def asset_for(kind: str) -> Optional[str]:
    return {
        "mac": MAC_ASSET,
        "windows-installed": WINDOWS_INSTALLER_ASSET,
        "windows-portable": WINDOWS_PORTABLE_ASSET,
    }.get(kind)


def release_from_json(data: dict, kind: str) -> Optional[Release]:
    """Pick out what the updater needs; None if the release lacks it."""
    if data.get("draft") or data.get("prerelease"):
        return None
    wanted = asset_for(kind)
    # A release is visible while its files are still uploading; skip any that
    # aren't finished, and the next check picks the release up.
    assets = {
        asset.get("name"): asset.get("browser_download_url")
        for asset in data.get("assets", [])
        if asset.get("state", "uploaded") == "uploaded"
    }
    if not wanted or wanted not in assets or CHECKSUM_ASSET not in assets:
        return None
    return Release(
        version=str(data.get("tag_name", "")).lstrip("v"),
        page_url=str(data.get("html_url", "")),
        asset_name=wanted,
        asset_url=str(assets[wanted]),
        checksum_url=str(assets[CHECKSUM_ASSET]),
        signature_url=str(assets.get(SIGNATURE_ASSET) or ""),
    )


def trusted_keys() -> list[PublicKey]:
    """The release keys, plus the test key when running from source."""
    keys = [parse_public_key(text) for text in RELEASE_KEYS]
    test_key = os.environ.get(TEST_KEY_ENV, "")
    if test_key and not getattr(sys, "frozen", False):
        keys.append(parse_public_key(test_key))
    return keys


def verified_claim(
    checksums: bytes, signature: bytes, version: str, keys: Optional[list[PublicKey]] = None
) -> ReleaseClaim:
    """What a release's signature says, once it checks out.

    The signature must be one of the release keys' over exactly these
    checksums, and its trusted comment must name this release's version, so a
    signed older release can't be passed off under a newer tag. Raises
    UpdateError with the message to show.
    """
    try:
        comment = verify(checksums, signature, trusted_keys() if keys is None else keys)
    except SignatureError as error:
        raise UpdateError("This update’s signature isn’t valid, so it can’t be installed.") from error
    claim = parse_claim(comment)
    if claim is None:
        raise UpdateError("This update isn’t signed for this version, so it can’t be installed.")
    if claim.version != version:
        raise UpdateError("This update’s signature is for another version, so it can’t be installed.")
    if not is_newer(claim.version, __version__):
        raise UpdateError("This update isn’t newer than the installed version, so it can’t be installed.")
    return claim


def bundle_path() -> Optional[Path]:
    """The .app bundle the running executable lives in (macOS)."""
    executable = Path(sys.executable).resolve()
    for parent in executable.parents:
        if parent.suffix == ".app":
            return parent
    return None


def install_location_problem(kind: str) -> str:
    """Why this copy can't replace itself, or "" when it can.

    Checked before anything is downloaded, so a copy that can never update
    says so at once instead of downloading the update every few hours.
    """
    if kind == "mac":
        bundle = bundle_path()
        if bundle is None:
            return "Couldn’t find the installed app."
        # The swap renames the bundle inside its folder. A copy opened from the
        # disk image, or translocated by Gatekeeper, is on a read-only volume,
        # which access() reports too.
        if os.access(bundle.parent, os.W_OK):
            return ""
        if bundle.parent in (Path("/Applications"), Path.home() / "Applications"):
            return "Your account can’t change apps in Applications. Ask an administrator to update it."
        return MOVE_TO_APPLICATIONS
    if kind in ("windows-installed", "windows-portable"):
        folder = Path(sys.executable).parent
        # On Windows access() reads only the read-only attribute, not the
        # folder's permissions, so try writing a file there.
        probe = folder / f".{WORKDIR_PREFIX}probe"
        try:
            probe.write_bytes(b"")
            probe.unlink()
        except OSError:
            return f"No permission to replace the app in {folder}."
    return ""


def bundle_version(app: Path) -> str:
    try:
        with open(app / "Contents" / "Info.plist", "rb") as handle:
            return str(plistlib.load(handle).get("CFBundleShortVersionString", ""))
    except (OSError, ValueError):  # plistlib.InvalidFileException is a ValueError
        return ""


def designated_requirement(app: Path) -> str:
    """The signature requirement macOS stores with privacy grants."""
    result = subprocess.run(
        ["codesign", "-d", "-r-", str(app)], capture_output=True, text=True, check=False
    )
    for line in (result.stdout + result.stderr).splitlines():
        if line.startswith("designated =>"):
            return line.split("=>", 1)[1].strip()
    return ""


def signature_is_valid(app: Path) -> bool:
    result = subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True, check=False
    )
    return result.returncode == 0


def requirement_is_stable(requirement: str) -> bool:
    """Ad-hoc signatures pin a hash of the files ("cdhash"), which changes with
    every build; only a certificate-based requirement survives an update."""
    return bool(requirement) and "certificate" in requirement and "cdhash" not in requirement


def requirement_accepted(installed: str, incoming: str, signed: str) -> bool:
    """May an app whose designated requirement is `incoming` replace one whose
    requirement is `installed`?

    It has to stay the same to keep the Accessibility grant, unless the
    release's signed comment names the new requirement (`signed`): that is how
    a release moves to a new signing identity. The new one must still be
    certificate-based, or the check would be off for every later update.
    """
    if not requirement_is_stable(installed) or incoming == installed:
        return True
    return incoming == signed and requirement_is_stable(incoming)


def mac_swap_script(
    pid: int,
    current: Path,
    staged: Path,
    relaunch_args: list[str],
    opener: str = "open",
    workdir: Optional[Path] = None,
) -> str:
    """Wait for the app to exit, move the new bundle into place, reopen it.

    The old bundle is kept until the new one is in place, and restored if the
    move fails, so a failed update never leaves the user without the app.
    """
    quoted = lambda value: "'" + str(value).replace("'", "'\\''") + "'"  # noqa: E731
    args = " ".join(quoted(argument) for argument in relaunch_args)
    backup = current.with_name(current.name + ".previous")
    # Only ever delete the updater's own download folder, never whatever
    # folder the script happens to sit in.
    if workdir is not None and workdir.name.startswith(WORKDIR_PREFIX):
        cleanup = f"rm -rf {quoted(workdir)}"
    else:
        cleanup = 'rm -f "$0"'
    return f"""#!/bin/bash
for _ in $(seq 1 150); do kill -0 {pid} 2>/dev/null || break; sleep 0.2; done
rm -rf {quoted(backup)}
if mv {quoted(current)} {quoted(backup)} && mv {quoted(staged)} {quoted(current)}; then
  rm -rf {quoted(backup)}
else
  [ -d {quoted(current)} ] || mv {quoted(backup)} {quoted(current)}
fi
xattr -dr com.apple.quarantine {quoted(current)} 2>/dev/null
{opener} {quoted(current)} --args {args}
{cleanup}
"""


def windows_portable_script(pid: int, relaunch_args: list[str]) -> str:
    """Swap the portable executable (%DCF_APP%) for the download (%DCF_SRC%)
    once the app has quit, then reopen it.

    The move is retried: in a one-file build the launcher process holds the
    executable for a moment after the app itself has exited. `ping` is the
    delay because `timeout` refuses to run without a console. After 30 failed
    tries the old copy is reopened rather than leaving the user with nothing.
    """
    args = " ".join(relaunch_args)
    return f"""@echo off
setlocal
set tries=0
:wait
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)
:move
move /Y "%{SOURCE_ENV}%" "%{APP_ENV}%" >nul 2>&1 && goto done
set /a tries+=1
if %tries% GEQ 30 goto done
ping -n 2 127.0.0.1 >nul
goto move
:done
start "" "%{APP_ENV}%" {args}
del "%~f0"
"""


def windows_installer_script(relaunch: str, relaunch_args: list[str]) -> str:
    """Run the installer (%DCF_SRC%) silently; if it fails, reopen the copy
    that was running (%DCF_APP%), so a failed update never leaves the user
    without the app. On success the installer relaunches the new copy itself."""
    flags = f"/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS {relaunch}"
    return f"""@echo off
setlocal
"%{SOURCE_ENV}%" {flags}
if errorlevel 1 start "" "%{APP_ENV}%" {" ".join(relaunch_args)}
del "%~f0"
"""


# -- the updater ------------------------------------------------------------------

class Updater(QObject):
    """Checks, downloads and installs updates; the UI follows `changed`."""

    changed = Signal()
    #: Emitted when the app should quit so the update can be applied.
    quit_requested = Signal()

    IDLE, CHECKING, CURRENT, AVAILABLE, DOWNLOADING, READY, INSTALLING, FAILED = (
        "idle", "checking", "current", "available", "downloading", "ready", "installing", "failed",
    )

    def __init__(self, controller, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.kind = installation_kind()
        self.state = self.IDLE
        self.message = ""
        self.release: Optional[Release] = None
        self.progress = 0.0
        self._network = QNetworkAccessManager(self)
        self._reply: Optional[QNetworkReply] = None
        self._workdir: Optional[Path] = None
        # A verified download waiting for a good moment to restart the app.
        self._ready_file: Optional[Path] = None
        self._unattended = False
        # From the verified checksums and signature of the update in progress.
        self._checksums: dict[str, str] = {}
        self._claim: Optional[ReleaseClaim] = None
        self._timer = QTimer(self)
        self._timer.setInterval(CHECK_INTERVAL_MS)
        self._timer.timeout.connect(lambda: self.check(user_initiated=False))

    # -- public -------------------------------------------------------------------
    @property
    def supported(self) -> bool:
        return self.kind != "source" or bool(os.environ.get(URL_OVERRIDE_ENV))

    @property
    def auto_check(self) -> bool:
        """Look for updates in the background. Until this is set, it follows
        the install switch: before the two were separate, turning that off
        stopped the checks too, and a copy updated from then still does."""
        settings = self.controller.settings
        return bool(settings.get("auto_check", settings.get("auto_update", True)))

    @property
    def auto_install(self) -> bool:
        """Install what a background check finds without asking. It has no
        effect while `auto_check` is off, since only background checks install
        on their own."""
        return bool(self.controller.settings.get("auto_update", True))

    def set_auto_check(self, enabled: bool) -> None:
        self._remember(auto_check=bool(enabled))
        self.changed.emit()

    def set_auto_install(self, enabled: bool) -> None:
        self.controller.set_auto_update(bool(enabled))
        if not enabled and self.state == self.READY:
            # It was going to install when the window closed; now it waits to
            # be asked, and the window says so.
            self._discard_download()
        else:
            self.changed.emit()

    def start(self) -> None:
        """Begin the background schedule."""
        if not self.supported:
            return
        remove_stale_workdirs()
        QTimer.singleShot(FIRST_CHECK_DELAY_MS, lambda: self.check(user_initiated=False))
        self._timer.start()

    def check(self, user_initiated: bool = True) -> None:
        # While READY a verified download is waiting for the window to close;
        # checking again would only download the same update a second time.
        if not self.supported or self.state in (self.CHECKING, self.DOWNLOADING, self.READY, self.INSTALLING):
            return
        if not user_initiated and not self.auto_check:
            return
        self._forget_release()
        self._set(self.CHECKING, "")
        request = self._request(os.environ.get(URL_OVERRIDE_ENV) or LATEST_URL)
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        self._reply = self._network.get(request)
        self._reply.finished.connect(lambda: self._step(self._on_checked, CHECK_FAILED, user_initiated))

    def install(self, unattended: bool = False) -> None:
        """Download the available update and apply it. `unattended` (a
        background update) waits rather than restart the app while its window
        is open or calibration is running."""
        if self.state == self.READY:
            self._apply(self._ready_file)
            return
        if self.release is None or self.state in (self.CHECKING, self.DOWNLOADING, self.INSTALLING):
            return
        # A release is only ever offered with its signature checked (see
        # _on_signature), but nothing unverified is downloaded either way.
        problem = UNSIGNED if self._claim is None else install_location_problem(self.kind)
        if problem:
            self._set(self.FAILED, problem)
            return
        self._unattended = unattended
        self._remove_workdir()
        try:
            self._workdir = Path(tempfile.mkdtemp(prefix=WORKDIR_PREFIX))
        except OSError:
            self._set(self.FAILED, "Couldn’t save the update.")
            return
        self._set(self.DOWNLOADING, "")
        self._download(self.release.asset_url, self._workdir / self.release.asset_name, self._on_asset)

    # -- steps --------------------------------------------------------------------
    def _step(self, step, failure: str, *args: object) -> None:
        """Run one step of a check or an update, as a network reply finishes.

        Whatever goes wrong ends the step in FAILED, showing `failure` unless
        it was an UpdateError, which says what to show. Nothing may leave the
        updater in CHECKING or DOWNLOADING, which would block every later
        check until the app restarts.
        """
        try:
            step(*args)
        except UpdateError as error:
            self._fail(str(error))
        except Exception:
            log.exception("Update step failed")
            self._fail(failure)

    def _request(self, url: str) -> QNetworkRequest:
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", f"DoubleClickFixer/{__version__}".encode())
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        request.setTransferTimeout(STALL_TIMEOUT_MS)
        return request

    def _on_checked(self, user_initiated: bool) -> None:
        reply = self._reply
        self._reply = None
        if reply is None:
            return
        reply.deleteLater()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            # 404: no release is published, or the repository can't be read
            # (it is private). Saying "Up to date" would hide that.
            if status == 404:
                self._set(self.FAILED, "No published releases were found.")
            else:
                self._set(self.FAILED, CHECK_FAILED)
            return
        try:
            data = json.loads(bytes(reply.readAll()).decode("utf-8"))
        except ValueError:
            self._set(self.FAILED, "Couldn’t read the update information.")
            return
        release = release_from_json(data, self.kind if self.kind != "source" else "mac")
        self.controller.set_last_update_check()
        if release is None or not is_newer(release.version, __version__):
            self._set(self.CURRENT, "")
            return
        # Anyone who can upload files to a GitHub release could announce any
        # version, so a release is offered only once its signature checks
        # out. It ends in FAILED rather than CURRENT when it doesn't: a newer
        # release is there, and calling this copy up to date would hide it
        # (and "Try Again" picks up a signature uploaded a moment late).
        if not release.signature_url:
            self._set(self.FAILED, UNSIGNED)
            return
        self._fetch(release.checksum_url, lambda checksums: self._on_checksums(release, checksums, user_initiated))

    def _fetch(self, url: str, done) -> None:
        """Download a small file of a check into memory and hand its bytes to
        `done`; a failure ends the check."""
        reply = self._network.get(self._request(url))
        self._reply = reply

        def on_progress(received: int, _total: int) -> None:
            if received > SMALL_FILE_LIMIT:
                reply.abort()  # so a huge file isn't held in memory

        def on_finished() -> None:
            reply.deleteLater()
            self._reply = None
            data = bytes(reply.readAll())
            # A file that arrives in one piece is finished before it can be
            # stopped, so its size is checked here too.
            if reply.error() != QNetworkReply.NetworkError.NoError or len(data) > SMALL_FILE_LIMIT:
                self._set(self.FAILED, CHECK_FAILED)
                return
            done(data)

        reply.downloadProgress.connect(on_progress)
        reply.finished.connect(lambda: self._step(on_finished, CHECK_FAILED))

    def _on_checksums(self, release: Release, checksums: bytes, user_initiated: bool) -> None:
        self._fetch(
            release.signature_url,
            lambda signature: self._on_signature(release, checksums, signature, user_initiated),
        )

    def _on_signature(self, release: Release, checksums: bytes, signature: bytes, user_initiated: bool) -> None:
        try:
            claim = verified_claim(checksums, signature, release.version)
        except UpdateError as error:
            self._set(self.FAILED, str(error))
            return
        self.release = release
        self._claim = claim
        # The download's hash comes from the exact bytes the signature covers.
        self._checksums = parse_checksums(checksums.decode("utf-8", errors="replace"))
        self._set(self.AVAILABLE, "")
        if not user_initiated and self.auto_install and self._attempts(release.version) < GIVE_UP_AFTER:
            self.install(unattended=True)

    def note_relaunch(self, result: str) -> None:
        """Say how the update that just restarted the app went."""
        if result == "updated":
            if self.controller.settings.get("update_attempt_version"):
                self._remember(update_attempt_version="", update_attempt_count=0)
            self._set(self.CURRENT, f"Updated to {__version__}")
        elif result == "failed":
            self._set(self.FAILED, INSTALL_FAILED)

    def apply_if_ready(self) -> None:
        """A good moment to restart (the window was closed): finish a
        background update that was waiting."""
        if self.state != self.READY or not self._unattended or self.controller.suspended:
            return
        if not self.auto_install:
            self._discard_download()
            return
        self._apply(self._ready_file)

    def _download(self, url: str, target: Path, done) -> None:
        try:
            handle = open(target, "wb")
        except OSError:
            self._fail("Couldn’t save the update.")
            return
        reply = self._network.get(self._request(url))
        self._reply = reply

        failed = []

        def write(data: bytes) -> bool:
            try:
                handle.write(data)
                return True
            except OSError:  # disk full, or the folder went away
                if not failed:
                    failed.append(True)
                    reply.abort()
                return False

        def on_ready() -> None:
            write(bytes(reply.readAll()))

        def on_progress(received: int, total: int) -> None:
            if total > 0:
                self.progress = received / total
                self.changed.emit()

        def on_finished() -> None:
            if not failed:
                write(bytes(reply.readAll()))
            try:
                handle.close()
            except OSError:
                failed.append(True)
            reply.deleteLater()
            self._reply = None
            if failed:
                self._fail("Couldn’t save the update. Check that the disk has free space.")
                return
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self._fail("The download didn’t finish.")
                return
            done(target)

        reply.readyRead.connect(on_ready)
        reply.downloadProgress.connect(on_progress)
        reply.finished.connect(lambda: self._step(on_finished, INSTALL_FAILED))

    def _on_asset(self, path: Path) -> None:
        if self.release is None:
            self._fail("The update was cancelled.")
            return
        expected = self._checksums.get(self.release.asset_name)
        if not expected or sha256_of(path) != expected:
            # Counted like a failed install: a release whose file never
            # matches would otherwise be downloaded in full every few hours.
            self._count_attempt()
            self._fail("The download didn’t match its checksum, so it wasn’t installed.")
            return
        if self._unattended and not self.auto_install:
            # Installing automatically was turned off while this downloaded.
            self._discard_download()
            return
        if self._unattended and (self.window_visible() or self.controller.suspended):
            # Don't restart the app under someone using it; finish when the
            # window closes, or when they press Restart Now.
            self._ready_file = path
            self._set(self.READY, "")
            return
        self._apply(path)

    def _apply(self, path: Optional[Path]) -> None:
        if path is None or self.release is None:
            return
        self._ready_file = None
        self._set(self.INSTALLING, "")
        try:
            # Counted before trying, so an attempt that never comes back (the
            # app quits and the new copy doesn't start) counts too. A
            # successful one changes the running version, after which the
            # count no longer applies.
            self._count_attempt()
            if self.kind == "mac":
                self._install_mac(path)
            elif self.kind == "windows-installed":
                self._install_windows_installer(path)
            elif self.kind == "windows-portable":
                self._install_windows_portable(path)
            else:
                self._fail("Updates install only into a packaged copy of the app.")
                return
        except UpdateError as error:
            self._fail(str(error))
            return
        except Exception:
            log.exception("Installing the update failed")
            self._fail(INSTALL_FAILED)
            return
        # Checked after the relaunch, to say whether it worked.
        self.controller.set_pending_update(self.release.version)
        self.quit_requested.emit()

    # -- platform installs ----------------------------------------------------------
    #: Set by the app: whether the window is open, so the relaunched copy
    #: comes back the same way (window or menu bar only).
    window_visible = staticmethod(lambda: True)

    def _relaunch_args(self) -> list[str]:
        return ["--updated"] if self.window_visible() else ["--updated", "--minimized"]

    def _install_mac(self, archive: Path) -> None:
        current = bundle_path()
        if current is None:
            raise UpdateError("Couldn’t find the installed app.")
        unpacked = archive.parent / "unpacked"
        result = subprocess.run(["ditto", "-x", "-k", str(archive), str(unpacked)], capture_output=True)
        if result.returncode != 0:
            raise UpdateError("Couldn’t unpack the update.")
        candidates = list(unpacked.glob("*.app"))
        if len(candidates) != 1:
            raise UpdateError("The update didn’t contain the app.")
        new_app = candidates[0]
        if not signature_is_valid(new_app):
            raise UpdateError("The update isn’t signed correctly, so it wasn’t installed.")
        assert self.release is not None
        if bundle_version(new_app) != self.release.version:
            raise UpdateError("The update isn’t the version it claims to be, so it wasn’t installed.")
        installed = designated_requirement(current)
        incoming = designated_requirement(new_app)
        if not requirement_accepted(installed, incoming, self._claim.requirement if self._claim else ""):
            raise UpdateError("The update is signed by someone else, so it wasn’t installed.")

        # Stage beside the current app, so the final move is a rename on the
        # same volume and can't be left half done.
        staged = current.with_name("." + current.stem + " update.app")
        shutil.rmtree(staged, ignore_errors=True)
        copied = subprocess.run(["ditto", str(new_app), str(staged)], capture_output=True)
        if copied.returncode != 0:
            shutil.rmtree(staged, ignore_errors=True)
            raise UpdateError(f"No permission to replace the app in {current.parent}.")
        script = archive.parent / "apply-update.sh"
        script.write_text(
            mac_swap_script(os.getpid(), current, staged, self._relaunch_args(), workdir=self._workdir)
        )
        subprocess.Popen(["/bin/bash", str(script)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _install_windows_installer(self, installer: Path) -> None:
        relaunch = "/RELAUNCH=1" if self.window_visible() else "/RELAUNCH=2"
        start_windows_script(
            installer.parent / "apply-update.cmd",
            windows_installer_script(relaunch, self._relaunch_args()),
            app=Path(sys.executable),
            source=installer,
        )

    def _install_windows_portable(self, downloaded: Path) -> None:
        # install_location_problem() already found that the folder is writable.
        start_windows_script(
            downloaded.parent / "apply-update.cmd",
            windows_portable_script(os.getpid(), self._relaunch_args()),
            app=Path(sys.executable),
            source=downloaded,
        )

    # -- state ------------------------------------------------------------------------
    def _attempts(self, version: str) -> int:
        """How many times installing `version` has been tried."""
        settings = self.controller.settings
        if settings.get("update_attempt_version") != version:
            return 0
        try:
            return max(0, int(settings.get("update_attempt_count", 0)))
        except (TypeError, ValueError):
            return 0

    def _count_attempt(self) -> None:
        """Note one more try at installing the release on offer."""
        if self.release is not None:
            version = self.release.version
            self._remember(update_attempt_version=version, update_attempt_count=self._attempts(version) + 1)

    def _remember(self, **values: object) -> None:
        # The updater's own settings are written by the controller with all the
        # others, so they survive its next save. A controller without
        # store_update_state predates it, and its _store does the same.
        store = getattr(self.controller, "store_update_state", None) or self.controller._store
        store(**values)

    def _remove_workdir(self) -> None:
        if self._workdir is not None:
            shutil.rmtree(self._workdir, ignore_errors=True)
            self._workdir = None

    def _forget_release(self) -> None:
        self.release = None
        self._claim = None
        self._checksums = {}

    def _discard_download(self) -> None:
        """Drop a waiting download: the update stays available, to install
        when the user asks."""
        self._remove_workdir()
        self._ready_file = None
        self._set(self.AVAILABLE, "")

    def _fail(self, message: str) -> None:
        self._remove_workdir()
        self._set(self.FAILED, message)

    def _set(self, state: str, message: str) -> None:
        self.state = state
        self.message = message
        if state != self.DOWNLOADING:
            self.progress = 0.0
        self.changed.emit()


class UpdateError(RuntimeError):
    pass


def start_windows_script(script: Path, text: str, app: Path, source: Path) -> None:
    """Write an update script as plain ASCII and run it detached, handing it
    the paths through its environment (see APP_ENV)."""
    try:
        script.write_text(text, encoding="ascii")
    except (OSError, UnicodeError) as error:
        raise UpdateError("Couldn’t prepare the update.") from error
    environment = {**os.environ, APP_ENV: str(app), SOURCE_ENV: str(source)}
    subprocess.Popen(["cmd", "/c", str(script)], env=environment, creationflags=_detached_flags())


def _detached_flags() -> int:
    if platform.system() != "Windows":
        return 0
    # CREATE_NO_WINDOW, not DETACHED_PROCESS: a detached cmd.exe would open a
    # fresh console window for every program it runs.
    return subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]


def remove_stale_workdirs(max_age_s: float = 24 * 60 * 60) -> None:
    """Delete update downloads left behind by earlier successful updates.

    The installer, or the swap script, still needs its files while the update
    is applied, so they cannot be removed at the time; the next launch does.
    """
    import time

    root = Path(tempfile.gettempdir())
    for folder in root.glob(WORKDIR_PREFIX + "*"):
        try:
            if time.time() - folder.stat().st_mtime > max_age_s:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass
