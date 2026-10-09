import hashlib
import http.server
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from run import _unhide_qt_plugins

_unhide_qt_plugins()

from app import settings as settings_store
from app import updater
from app.updater import (
    CHECKSUM_ASSET,
    MAC_ASSET,
    SIGNATURE_ASSET,
    WINDOWS_INSTALLER_ASSET,
    is_newer,
    mac_swap_script,
    parse_checksums,
    parse_version,
    release_from_json,
    requirement_accepted,
    requirement_is_stable,
    sha256_of,
    verified_claim,
)
from app.update_signature import parse_public_key
from tools.sign_release import public_key_of, public_key_text, signature_text

# A throwaway release key for this run. The updater trusts it only through
# DCF_UPDATE_TEST_KEY, and only when running from source.
TEST_SEED, TEST_KEY_ID = os.urandom(32), os.urandom(8)
TEST_KEY = public_key_text(TEST_KEY_ID, public_key_of(TEST_SEED), "test").splitlines()[1]


def sign(checksums: bytes, version: str = "9.9.9", comment: str = "") -> bytes:
    """A signature of `checksums` by the test key, for release `version`."""
    return signature_text(checksums, TEST_KEY_ID, TEST_SEED, comment or f"dcf {version}")


def release_json(
    version="0.3.0", assets=(MAC_ASSET, WINDOWS_INSTALLER_ASSET, CHECKSUM_ASSET, SIGNATURE_ASSET), base="https://x"
):
    return {
        "tag_name": f"v{version}",
        "html_url": f"{base}/releases/v{version}",
        "draft": False,
        "prerelease": False,
        "assets": [{"name": name, "browser_download_url": f"{base}/{name}"} for name in assets],
    }


class VersionTests(unittest.TestCase):
    def test_parse(self) -> None:
        self.assertEqual(parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(parse_version("0.10"), (0, 10))
        self.assertEqual(parse_version("garbage"), (0,))

    def test_ordering_is_numeric_not_textual(self) -> None:
        self.assertTrue(is_newer("0.10.0", "0.9.9"))
        self.assertTrue(is_newer("1.0", "0.99.99"))
        self.assertFalse(is_newer("0.2.0", "0.2"))
        self.assertFalse(is_newer("0.1.9", "0.2.0"))


class ReleaseTests(unittest.TestCase):
    def test_picks_the_asset_for_this_installation(self) -> None:
        release = release_from_json(release_json(), "mac")
        self.assertEqual(release.version, "0.3.0")
        self.assertEqual(release.asset_name, MAC_ASSET)
        self.assertEqual(release.signature_url, f"https://x/{SIGNATURE_ASSET}")
        windows = release_from_json(release_json(), "windows-installed")
        self.assertEqual(windows.asset_name, WINDOWS_INSTALLER_ASSET)

    def test_a_release_without_checksums_is_ignored(self) -> None:
        self.assertIsNone(release_from_json(release_json(assets=(MAC_ASSET,)), "mac"))

    def test_an_unsigned_release_is_still_seen(self) -> None:
        # Seen, so that the check can say why it isn't offered.
        release = release_from_json(release_json(assets=(MAC_ASSET, CHECKSUM_ASSET)), "mac")
        self.assertEqual(release.signature_url, "")

    def test_drafts_and_prereleases_are_ignored(self) -> None:
        for flag in ("draft", "prerelease"):
            data = release_json()
            data[flag] = True
            self.assertIsNone(release_from_json(data, "mac"))

    def test_missing_platform_asset(self) -> None:
        self.assertIsNone(release_from_json(release_json(assets=(WINDOWS_INSTALLER_ASSET, CHECKSUM_ASSET)), "mac"))

    def test_checksum_file_parsing(self) -> None:
        digest = "a" * 64
        parsed = parse_checksums(f"{digest}  {MAC_ASSET}\n{digest.upper()} *other.exe\nnot a line\n")
        self.assertEqual(parsed, {MAC_ASSET: digest, "other.exe": digest})


class SignatureTests(unittest.TestCase):
    def test_only_certificate_requirements_survive_updates(self) -> None:
        self.assertTrue(
            requirement_is_stable('identifier "com.doubleclickfixer.app" and certificate root = H"81a5"')
        )
        self.assertFalse(requirement_is_stable('cdhash H"0f3c"'))
        self.assertFalse(requirement_is_stable(""))

    def test_a_new_requirement_needs_the_signed_comment_to_name_it(self) -> None:
        old = 'identifier "com.doubleclickfixer.app" and certificate root = H"81a5"'
        new = 'anchor apple generic and identifier "com.doubleclickfixer.app" and certificate leaf[subject.OU] = X'
        self.assertTrue(requirement_accepted(old, old, ""))
        self.assertFalse(requirement_accepted(old, new, ""), "someone else's signature")
        self.assertTrue(requirement_accepted(old, new, new), "the release names the move")
        self.assertFalse(requirement_accepted(old, new, new + " "), "it must name exactly that requirement")
        self.assertFalse(requirement_accepted(old, 'cdhash H"0f3c"', 'cdhash H"0f3c"'), "never to an unstable one")
        # A development build signed ad hoc has nothing stable to keep.
        self.assertTrue(requirement_accepted('cdhash H"0f3c"', new, ""))


class ReleaseSignatureTests(unittest.TestCase):
    """The checks on a release's minisign signature (item 10 of the 1.0 plan)."""

    checksums = f"{'a' * 64}  {MAC_ASSET}\n".encode()
    keys = [parse_public_key(TEST_KEY)]

    def test_a_good_signature_gives_its_claim(self) -> None:
        claim = verified_claim(self.checksums, sign(self.checksums), "9.9.9", self.keys)
        self.assertEqual((claim.version, claim.requirement), ("9.9.9", ""))
        moved = sign(self.checksums, comment='dcf 9.9.9 dr=identifier "x" and certificate leaf = H"01"')
        self.assertEqual(
            verified_claim(self.checksums, moved, "9.9.9", self.keys).requirement,
            'identifier "x" and certificate leaf = H"01"',
        )

    def test_a_forged_signature_is_refused(self) -> None:
        from tools.sign_release import signature_text as make

        forged = make(self.checksums, TEST_KEY_ID, os.urandom(32), "dcf 9.9.9")  # same key id, other key
        with self.assertRaisesRegex(updater.UpdateError, "signature isn’t valid"):
            verified_claim(self.checksums, forged, "9.9.9", self.keys)
        tampered = self.checksums.replace(b"a", b"b")
        with self.assertRaisesRegex(updater.UpdateError, "signature isn’t valid"):
            verified_claim(tampered, sign(self.checksums), "9.9.9", self.keys)

    def test_only_the_built_in_keys_are_trusted_by_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(updater.TEST_KEY_ENV, None)
            with self.assertRaisesRegex(updater.UpdateError, "signature isn’t valid"):
                verified_claim(self.checksums, sign(self.checksums), "9.9.9")

    def test_a_comment_for_another_version_is_refused(self) -> None:
        for comment in ("dcf 9.9.8", "dcf 9.9.9.1", "dcf 9.9.9x", "dcf v9.9.9"):
            signature = signature_text(self.checksums, TEST_KEY_ID, TEST_SEED, comment)
            with self.assertRaisesRegex(updater.UpdateError, "is for another version", msg=comment):
                verified_claim(self.checksums, signature, "9.9.9", self.keys)
        # A comment that names no version at all.
        for comment in ("dcf  9.9.9", "9.9.9", "", "dcf"):
            signature = signature_text(self.checksums, TEST_KEY_ID, TEST_SEED, comment)
            with self.assertRaisesRegex(updater.UpdateError, "isn’t signed for this version", msg=comment):
                verified_claim(self.checksums, signature, "9.9.9", self.keys)

    def test_an_older_version_is_refused(self) -> None:
        from app import __version__

        for version in ("0.0.1", __version__):
            with self.assertRaisesRegex(updater.UpdateError, "isn’t newer"):
                verified_claim(self.checksums, sign(self.checksums, version), version, self.keys)

    def test_the_test_key_counts_only_when_running_from_source(self) -> None:
        with mock.patch.dict(os.environ, {updater.TEST_KEY_ENV: TEST_KEY}):
            self.assertIn(parse_public_key(TEST_KEY), updater.trusted_keys())
            with mock.patch.object(sys, "frozen", True, create=True):
                self.assertNotIn(parse_public_key(TEST_KEY), updater.trusted_keys())
                self.assertEqual(len(updater.trusted_keys()), 2)

    def test_the_built_in_keys_are_the_published_ones(self) -> None:
        folder = Path(__file__).resolve().parents[1] / "tools" / "keys"
        published = {path.stem: parse_public_key(path.read_text()) for path in folder.glob("*.pub")}
        self.assertEqual(
            [parse_public_key(text) for text in updater.RELEASE_KEYS],
            [published["primary"], published["backup"]],
        )


class InstallLocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_mac_copy_in_a_writable_folder(self) -> None:
        app = self.root / "DoubleClick Fixer.app"
        app.mkdir()
        with mock.patch.object(updater, "bundle_path", return_value=app):
            self.assertEqual(updater.install_location_problem("mac"), "")

    @unittest.skipIf(sys.platform == "win32" or os.geteuid() == 0, "POSIX permissions, not root")
    def test_mac_copy_that_cant_be_replaced_says_move_it(self) -> None:
        app = self.root / "image" / "DoubleClick Fixer.app"
        app.mkdir(parents=True)
        app.parent.chmod(0o555)  # like the read-only disk image
        self.addCleanup(app.parent.chmod, 0o755)
        with mock.patch.object(updater, "bundle_path", return_value=app):
            self.assertEqual(updater.install_location_problem("mac"), updater.MOVE_TO_APPLICATIONS)

    def test_mac_copy_in_applications_without_permission_needs_an_administrator(self) -> None:
        with mock.patch.object(updater, "bundle_path", return_value=Path("/Applications/DoubleClick Fixer.app")), \
                mock.patch.object(updater.os, "access", return_value=False):
            self.assertIn("administrator", updater.install_location_problem("mac"))
        with mock.patch.object(updater, "bundle_path", return_value=None):
            self.assertIn("Couldn’t find", updater.install_location_problem("mac"))

    def test_mac_copy_in_the_users_own_applications_folder(self) -> None:
        # Their own folder: no administrator to ask, and nowhere to move it.
        own = Path.home() / "Applications"
        with mock.patch.object(updater, "bundle_path", return_value=own / "DoubleClick Fixer.app"), \
                mock.patch.object(updater.os, "access", return_value=False):
            self.assertEqual(updater.install_location_problem("mac"), f"No permission to replace the app in {own}.")

    def test_windows_folder_is_probed_by_writing(self) -> None:
        exe = self.root / "DoubleClickFixer.exe"
        with mock.patch.object(updater.sys, "executable", str(exe)):
            self.assertEqual(updater.install_location_problem("windows-portable"), "")
            self.assertEqual(list(self.root.iterdir()), [], "the probe is removed")
            with mock.patch.object(Path, "write_bytes", side_effect=PermissionError):
                self.assertIn("No permission", updater.install_location_problem("windows-installed"))


class MacInstallTests(unittest.TestCase):
    """_install_mac up to starting the swap script, with ditto and codesign faked."""

    OLD = 'identifier "com.doubleclickfixer.app" and certificate root = H"81a5"'
    NEW = 'anchor apple generic and identifier "com.doubleclickfixer.app" and certificate leaf[subject.OU] = X'

    def install(self, bundled_version="9.9.9", incoming=OLD, claim=updater.ReleaseClaim("9.9.9")):
        from app.updater import Release, Updater

        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        current = root / "Applications" / "DoubleClick Fixer.app"
        current.mkdir(parents=True)
        workdir = root / (updater.WORKDIR_PREFIX + "1")
        workdir.mkdir()

        def run(command, **_kwargs):
            if command[:3] == ["ditto", "-x", "-k"]:  # unpack the zip
                contents = Path(command[4]) / "DoubleClick Fixer.app" / "Contents"
                contents.mkdir(parents=True)
                info = {"CFBundleShortVersionString": bundled_version}
                (contents / "Info.plist").write_bytes(plistlib.dumps(info))
            return subprocess.CompletedProcess(command, 0, b"", b"")

        instance = Updater(FakeController())
        self.addCleanup(instance.deleteLater)
        instance.release = Release("9.9.9", "", MAC_ASSET, "", "", "")
        instance._workdir = workdir
        instance._claim = claim
        requirement = lambda app: self.OLD if app == current else incoming  # noqa: E731
        with mock.patch.object(updater, "bundle_path", return_value=current), \
                mock.patch.object(updater.subprocess, "run", side_effect=run), \
                mock.patch.object(updater.subprocess, "Popen") as popen, \
                mock.patch.object(updater, "signature_is_valid", return_value=True), \
                mock.patch.object(updater, "designated_requirement", side_effect=requirement):
            instance._install_mac(workdir / MAC_ASSET)
        return popen

    def test_bundle_version_comes_from_info_plist(self) -> None:
        app = Path(tempfile.mkdtemp()) / "New.app"
        self.addCleanup(shutil.rmtree, app.parent, True)
        (app / "Contents").mkdir(parents=True)
        self.assertEqual(updater.bundle_version(app), "")
        (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "9.9.9"}))
        self.assertEqual(updater.bundle_version(app), "9.9.9")
        (app / "Contents" / "Info.plist").write_bytes(b"not a plist")
        self.assertEqual(updater.bundle_version(app), "")

    def test_a_matching_update_is_staged_and_swapped(self) -> None:
        self.install().assert_called_once()

    def test_the_bundle_must_be_the_release_version(self) -> None:
        with self.assertRaisesRegex(updater.UpdateError, "isn’t the version it claims"):
            self.install(bundled_version="9.9.8")

    def test_a_new_signing_identity_needs_the_signed_comment(self) -> None:
        with self.assertRaisesRegex(updater.UpdateError, "signed by someone else"):
            self.install(incoming=self.NEW)
        self.install(incoming=self.NEW, claim=updater.ReleaseClaim("9.9.9", self.NEW)).assert_called_once()


@unittest.skipUnless(sys.platform != "win32", "bash script")
class SwapScriptTests(unittest.TestCase):
    def test_script_replaces_the_bundle_and_relaunches(self) -> None:
        root = Path(tempfile.mkdtemp())
        current = root / "It's DoubleClick Fixer.app"  # spaces and a quote
        staged = root / ".It's DoubleClick Fixer update.app"
        (current / "Contents").mkdir(parents=True)
        (current / "Contents" / "version").write_text("old")
        (staged / "Contents").mkdir(parents=True)
        (staged / "Contents" / "version").write_text("new")
        marker = root / "relaunched"
        finished = subprocess.Popen(["true"])
        finished.wait()
        dead_pid = finished.pid
        script = root / "apply.sh"
        script.write_text(
            mac_swap_script(dead_pid, current, staged, ["--updated"], opener=f"touch {marker} #")
        )
        subprocess.run(["/bin/bash", str(script)], check=True)
        self.assertEqual((current / "Contents" / "version").read_text(), "new")
        self.assertFalse(staged.exists())
        self.assertFalse(current.with_name(current.name + ".previous").exists())
        self.assertTrue(marker.exists(), "the app is reopened")

    def test_script_removes_only_the_updaters_own_folder(self) -> None:
        root = Path(tempfile.mkdtemp())
        workdir = root / (updater.WORKDIR_PREFIX + "abc")
        workdir.mkdir()
        (workdir / "download.zip").write_text("x")
        current = root / "App.app"
        staged = root / ".App update.app"
        current.mkdir()
        staged.mkdir()
        finished = subprocess.Popen(["true"])
        finished.wait()
        script = workdir / "apply.sh"
        script.write_text(mac_swap_script(finished.pid, current, staged, [], opener="true", workdir=workdir))
        subprocess.run(["/bin/bash", str(script)], check=True)
        self.assertFalse(workdir.exists(), "the download folder is cleaned up")
        self.assertTrue(current.exists(), "the app next to it is untouched")

        # A folder without the updater's prefix is never deleted.
        other = root / "Applications"
        other.mkdir()
        text = mac_swap_script(finished.pid, current, staged, [], opener="true", workdir=other)
        self.assertNotIn("rm -rf '" + str(other), text)


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True


class FakeController:
    """The settings side of AppController, kept in memory. `settings` are
    what the settings file holds; they are read the way the app reads them,
    defaults and all."""

    def __init__(self, **settings) -> None:
        self.settings = settings_store.coerce(settings)
        self.suspended = False

    def _store(self, **values) -> None:
        self.settings = settings_store.coerce({**self.settings, **values})

    def set_auto_update(self, enabled: bool) -> None:
        self._store(auto_update=bool(enabled))

    def set_last_update_check(self) -> None:
        pass

    def set_pending_update(self, version: str) -> None:
        self._store(pending_update=version)

    def store_update_state(self, **values) -> None:
        self._store(**values)


@unittest.skipIf(os.environ.get("QT_QPA_PLATFORM") != "offscreen", "needs offscreen Qt")
class NetworkTests(unittest.TestCase):
    """The real updater against a local stand-in for GitHub."""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        # Stand-in releases are signed with the test key; the updater still
        # checks every signature.
        environment = mock.patch.dict(os.environ, {updater.TEST_KEY_ENV: TEST_KEY})
        environment.start()
        self.addCleanup(environment.stop)
        # The test runs as a "mac" copy without being inside an app bundle.
        location = mock.patch.object(updater, "install_location_problem", return_value="")
        self.location_problem = location.start()
        self.addCleanup(location.stop)
        self.requested: list[str] = []

    def serve(self, files: dict[str, bytes]) -> str:
        requested = self.requested

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                requested.append(self.path.lstrip("/"))
                body = files.get(self.path.lstrip("/"))
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = _Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def make_updater(self, url: str, **settings):
        from app.updater import Updater

        with mock.patch.dict(os.environ, {updater.URL_OVERRIDE_ENV: url}):
            instance = Updater(FakeController(**settings))
            instance.kind = "mac"
        self.addCleanup(instance.deleteLater)
        return instance

    def wait_for(self, instance, states, timeout=10.0) -> None:
        deadline = time.time() + timeout
        while instance.state not in states and time.time() < deadline:
            self.application.processEvents()
            time.sleep(0.01)
        self.assertIn(instance.state, states, instance.message)

    def settle(self, seconds=0.3) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.application.processEvents()
            time.sleep(0.01)

    def release_files(self, payload=b"pretend archive", version="9.9.9", checksum="", signature=None):
        """A served release of `payload`, signed by the test key unless
        `signature` says otherwise (None: signed, b"": no signature file)."""
        files: dict[str, bytes] = {MAC_ASSET: payload}
        base = self.serve(files)
        assets = (MAC_ASSET, CHECKSUM_ASSET) + ((SIGNATURE_ASSET,) if signature != b"" else ())
        files["latest"] = json.dumps(release_json(version, assets=assets, base=base)).encode()
        path = Path(tempfile.mkdtemp()) / "p"
        path.write_bytes(payload)
        files[CHECKSUM_ASSET] = f"{checksum or sha256_of(path)}  {MAC_ASSET}\n".encode()
        if signature != b"":
            files[SIGNATURE_ASSET] = sign(files[CHECKSUM_ASSET], version) if signature is None else signature
        return base, files

    def matching_files(self, payload=b"pretend archive"):
        return self.release_files(payload)[0]

    def available(self, base: str, **settings):
        """An updater that has found the release at `base`."""
        instance = self.make_updater(f"{base}/latest", **settings)
        with mock.patch.dict(os.environ, {updater.URL_OVERRIDE_ENV: f"{base}/latest"}):
            instance.check(user_initiated=True)
            self.wait_for(instance, {instance.AVAILABLE, instance.FAILED})
        self.assertEqual(instance.state, instance.AVAILABLE, instance.message)
        return instance

    def test_finds_a_newer_release(self) -> None:
        base = self.matching_files()
        instance = self.available(base)
        self.assertEqual(instance.release.version, "9.9.9")
        self.assertEqual(instance._claim.version, "9.9.9")
        self.assertEqual(self.requested, ["latest", CHECKSUM_ASSET, SIGNATURE_ASSET], "only the small files")

    def test_same_version_is_up_to_date(self) -> None:
        from app import __version__

        files: dict[str, bytes] = {}
        base = self.serve(files)
        files["latest"] = json.dumps(release_json(__version__, base=base)).encode()
        instance = self.make_updater(f"{base}/latest")
        with mock.patch.dict(os.environ, {updater.URL_OVERRIDE_ENV: f"{base}/latest"}):
            instance.check(user_initiated=True)
            self.wait_for(instance, {instance.CURRENT, instance.FAILED})
        self.assertEqual(instance.state, instance.CURRENT)

    def test_a_tampered_download_is_refused(self) -> None:
        base, _files = self.release_files(checksum="0" * 64)  # signed, but not the file served
        instance = self.available(base)
        instance.install()
        self.wait_for(instance, {instance.FAILED, instance.INSTALLING})
        self.assertEqual(instance.state, instance.FAILED)
        self.assertIn("checksum", instance.message)
        self.assertEqual(instance.controller.settings["update_attempt_count"], 1, "it counts as a failed attempt")

    def test_files_swapped_after_the_check_are_refused(self) -> None:
        # The download is held to the checksums whose signature the check
        # verified, whatever the release holds by the time it is installed.
        base, files = self.release_files()
        instance = self.available(base)
        files[MAC_ASSET] = b"something else"
        files[CHECKSUM_ASSET] = f"{hashlib.sha256(b'something else').hexdigest()}  {MAC_ASSET}\n".encode()
        with mock.patch.object(instance, "_install_mac") as install:
            instance.install()
            self.wait_for(instance, {instance.FAILED, instance.INSTALLING})
        self.assertEqual(instance.state, instance.FAILED)
        self.assertIn("checksum", instance.message)
        install.assert_not_called()

    def test_download_that_matches_reaches_install(self) -> None:
        base = self.matching_files()
        instance = self.available(base)
        with mock.patch.object(instance, "_install_mac") as install:
            instance.install()
            self.wait_for(instance, {instance.INSTALLING, instance.FAILED})
            deadline = time.time() + 2
            while not install.called and time.time() < deadline:
                self.application.processEvents()
        self.assertEqual(instance.state, instance.INSTALLING, instance.message)
        install.assert_called_once()
        self.assertEqual(instance._claim.version, "9.9.9")

    def refused(self, base: str, user_initiated: bool = True, **settings):
        """An updater whose check of the release at `base` ended in FAILED."""
        instance = self.make_updater(f"{base}/latest", **settings)
        instance.window_visible = lambda: False
        install = mock.patch.object(instance, "_install_mac").start()
        self.addCleanup(mock.patch.stopall)
        with mock.patch.dict(os.environ, {updater.URL_OVERRIDE_ENV: f"{base}/latest"}):
            instance.check(user_initiated=user_initiated)
            self.wait_for(instance, {instance.AVAILABLE, instance.FAILED, instance.INSTALLING})
            # Nothing it could install is offered: no "Update to" in the
            # menus, and Update Now does nothing.
            self.assertEqual(instance.state, instance.FAILED, instance.message)
            self.assertIsNone(instance.release)
            instance.install()
            self.settle()
        install.assert_not_called()
        self.assertNotIn(MAC_ASSET, self.requested, "nothing unverified is downloaded")
        self.assertIsNone(instance._workdir)
        return instance

    def test_an_unsigned_release_is_not_offered(self) -> None:
        base, _files = self.release_files(signature=b"")
        instance = self.refused(base)
        self.assertEqual(instance.message, "This update isn’t signed, so it can’t be installed.")
        self.assertEqual(self.requested, ["latest"])

    def test_a_forged_signature_is_not_offered(self) -> None:
        checksums = b"anything"
        forged = signature_text(checksums, TEST_KEY_ID, os.urandom(32), "dcf 9.9.9")
        base, _files = self.release_files(signature=forged)
        self.assertIn("signature isn’t valid", self.refused(base).message)

    def test_a_forged_release_isnt_installed_in_the_background(self) -> None:
        forged = signature_text(b"anything", TEST_KEY_ID, os.urandom(32), "dcf 9.9.9")
        base, _files = self.release_files(signature=forged)
        instance = self.refused(base, user_initiated=False, auto_update=True)
        self.assertIn("signature isn’t valid", instance.message)
        self.assertEqual(instance.controller.settings.get("update_attempt_count", 0), 0, "no attempt counted")

    def test_a_signature_of_other_checksums_is_not_offered(self) -> None:
        base, _files = self.release_files(signature=sign(f"{'1' * 64}  {MAC_ASSET}\n".encode()))
        self.assertIn("signature isn’t valid", self.refused(base).message)

    def test_a_signature_for_another_version_is_not_offered(self) -> None:
        # The genuine checksums, signed for an older release.
        base, files = self.release_files()
        files[SIGNATURE_ASSET] = sign(files[CHECKSUM_ASSET], "9.9.8")
        self.assertIn("is for another version", self.refused(base).message)

    def test_a_signature_without_a_version_is_not_offered(self) -> None:
        base, files = self.release_files()
        files[SIGNATURE_ASSET] = sign(files[CHECKSUM_ASSET], comment="release")
        self.assertEqual(self.refused(base).message, "This update isn’t signed for this version, so it can’t be installed.")

    def test_a_signature_that_cant_be_fetched_ends_the_check(self) -> None:
        base, files = self.release_files()
        del files[SIGNATURE_ASSET]  # listed, but the server says 404
        self.assertEqual(self.refused(base).message, updater.CHECK_FAILED)

    def test_an_oversized_checksum_file_is_stopped(self) -> None:
        base, files = self.release_files()
        files[CHECKSUM_ASSET] = b"0" * (updater.SMALL_FILE_LIMIT * 4)
        self.assertEqual(self.refused(base).message, updater.CHECK_FAILED)
        self.assertNotIn(SIGNATURE_ASSET, self.requested)

    def test_an_unexpected_error_while_verifying_ends_the_check(self) -> None:
        # What a packaged build without hashlib's _blake2 module would do.
        base = self.matching_files()
        missing = AttributeError("module 'hashlib' has no attribute 'blake2b'")
        with mock.patch.object(updater, "verified_claim", side_effect=missing), \
                self.assertLogs("app.updater", "ERROR") as logged:
            instance = self.refused(base)
        self.assertEqual(instance.message, updater.CHECK_FAILED)
        self.assertIn("blake2b", "\n".join(logged.output))
        # The next check isn't blocked.
        with mock.patch.dict(os.environ, {updater.URL_OVERRIDE_ENV: f"{base}/latest"}):
            instance.check(user_initiated=True)
            self.wait_for(instance, {instance.AVAILABLE, instance.FAILED})
        self.assertEqual(instance.state, instance.AVAILABLE, instance.message)

    def test_a_copy_that_cant_replace_itself_says_so_before_downloading(self) -> None:
        base = self.matching_files()
        instance = self.available(base)
        self.location_problem.return_value = updater.MOVE_TO_APPLICATIONS
        instance.install(unattended=True)
        self.assertEqual(instance.state, instance.FAILED)
        self.assertEqual(instance.message, "Move DoubleClick Fixer to Applications to update it.")
        self.settle()
        self.assertEqual(self.requested, ["latest", CHECKSUM_ASSET, SIGNATURE_ASSET], "only the check's files")

    def test_a_new_download_replaces_the_previous_folder(self) -> None:
        base = self.matching_files()
        instance = self.available(base)
        previous = Path(tempfile.mkdtemp(prefix=updater.WORKDIR_PREFIX))
        instance._workdir = previous
        with mock.patch.object(instance, "_install_mac"):
            instance.install()
            self.assertFalse(previous.exists())
            self.wait_for(instance, {instance.INSTALLING, instance.FAILED})
        self.assertNotEqual(instance._workdir, previous)

    def test_an_unexpected_error_while_downloading_doesnt_leave_it_stuck(self) -> None:
        base = self.matching_files()
        instance = self.available(base)
        with mock.patch.object(updater, "sha256_of", side_effect=RuntimeError("boom")), \
                self.assertLogs("app.updater", "ERROR"):
            instance.install()
            self.wait_for(instance, {instance.FAILED, instance.INSTALLING, instance.READY})
        self.assertEqual((instance.state, instance.message), (instance.FAILED, updater.INSTALL_FAILED))
        self.assertIsNone(instance._workdir, "the download folder is removed")

    def test_an_unexpected_error_while_installing_doesnt_leave_it_stuck(self) -> None:
        base = self.matching_files()
        instance = self.available(base)
        quits: list[bool] = []
        instance.quit_requested.connect(lambda: quits.append(True))
        with mock.patch.object(instance, "_install_mac", side_effect=FileNotFoundError("ditto")), \
                self.assertLogs("app.updater", "ERROR"):
            instance.install()
            self.wait_for(instance, {instance.FAILED, instance.READY})
        self.assertEqual((instance.state, instance.message), (instance.FAILED, updater.INSTALL_FAILED))
        self.assertEqual(quits, [], "the app isn't quit for an update that didn't start")

    def test_restart_now_failing_doesnt_leave_it_stuck(self) -> None:
        # READY is reached without _step around _apply, so its own catch is
        # what keeps INSTALLING from sticking.
        base = self.matching_files()
        instance = self.available(base, auto_update=True)
        instance.window_visible = lambda: True
        with mock.patch.object(instance, "_install_mac", side_effect=FileNotFoundError("ditto")), \
                self.assertLogs("app.updater", "ERROR"):
            instance.install(unattended=True)
            self.wait_for(instance, {instance.READY, instance.FAILED})
            self.assertEqual(instance.state, instance.READY, instance.message)
            instance.install()  # Restart Now
            self.wait_for(instance, {instance.FAILED})
        self.assertEqual((instance.state, instance.message), (instance.FAILED, updater.INSTALL_FAILED))

    def test_a_check_that_finds_an_unsigned_release_forgets_the_old_one(self) -> None:
        base = self.matching_files()
        instance = self.available(base)
        self.assertIsNotNone(instance.release)
        unsigned, _files = self.release_files(version="9.9.10", signature=b"")
        with mock.patch.object(instance, "_install_mac") as install, \
                mock.patch.dict(os.environ, {updater.URL_OVERRIDE_ENV: f"{unsigned}/latest"}):
            instance.check(user_initiated=True)
            self.wait_for(instance, {instance.FAILED, instance.AVAILABLE})
            self.assertIsNone(instance.release, "the earlier release is no longer offered")
            instance.install()
            self.settle()
        install.assert_not_called()

    def test_a_full_disk_fails_instead_of_hanging(self) -> None:
        base = self.matching_files()
        instance = self.available(base)
        real_open = open

        class FullDisk:
            def __init__(self, handle):
                self.handle = handle

            def write(self, _data):
                raise OSError(28, "No space left on device")

            def close(self):
                self.handle.close()

        def opener(path, mode="r", *args, **kwargs):
            handle = real_open(path, mode, *args, **kwargs)
            return FullDisk(handle) if "w" in mode and str(path).endswith(MAC_ASSET) else handle

        with mock.patch("builtins.open", opener):
            instance.install()
            self.wait_for(instance, {instance.FAILED, instance.INSTALLING})
        self.assertEqual(instance.state, instance.FAILED)
        self.assertIn("free space", instance.message)
        self.assertIsNone(instance._reply)

    def ready_update(self, **settings):
        """A background update downloaded while the window is open."""
        base = self.matching_files()
        instance = self.available(base, auto_update=True, **settings)
        instance.window_visible = lambda: True
        install = mock.patch.object(instance, "_install_mac").start()
        self.addCleanup(mock.patch.stopall)
        instance.install(unattended=True)
        self.wait_for(instance, {instance.READY, instance.FAILED})
        install.assert_not_called()
        return instance, install, base

    def test_a_background_update_waits_while_the_window_is_open(self) -> None:
        instance, install, _base = self.ready_update()
        instance.window_visible = lambda: False
        instance.apply_if_ready()  # the window was closed
        install.assert_called_once()
        self.assertEqual(instance.state, instance.INSTALLING)

    def test_checks_wait_while_an_update_is_ready(self) -> None:
        instance, _install, base = self.ready_update()
        before = list(self.requested)
        with mock.patch.dict(os.environ, {updater.URL_OVERRIDE_ENV: f"{base}/latest"}):
            instance.check(user_initiated=False)
            instance.check(user_initiated=True)
            self.settle()
        self.assertEqual(instance.state, instance.READY)
        self.assertEqual(self.requested, before, "nothing is downloaded again")
        self.assertTrue(instance._ready_file.exists())

    def test_turning_off_automatic_installs_keeps_a_ready_update_waiting_for_the_user(self) -> None:
        instance, install, _base = self.ready_update()
        workdir = instance._workdir
        instance.controller.set_auto_update(False)  # switched off while it waited
        instance.window_visible = lambda: False
        instance.apply_if_ready()
        install.assert_not_called()
        self.assertEqual(instance.state, instance.AVAILABLE)
        self.assertFalse(workdir.exists())

        instance, install, _base = self.ready_update()
        instance.set_auto_install(False)
        self.assertEqual(instance.state, instance.AVAILABLE)
        self.assertFalse(instance.controller.settings["auto_update"])
        instance.install()  # the user asks for it
        self.wait_for(instance, {instance.INSTALLING, instance.FAILED})
        self.assertEqual(instance.state, instance.INSTALLING, instance.message)

    def test_turning_off_automatic_installs_during_a_download_stops_short_of_installing(self) -> None:
        base = self.matching_files()
        instance = self.available(base, auto_update=True)
        instance.window_visible = lambda: False
        with mock.patch.object(instance, "_install_mac") as install:
            instance.install(unattended=True)
            self.assertEqual(instance.state, instance.DOWNLOADING)
            workdir = instance._workdir
            instance.set_auto_install(False)  # before any of the download has arrived
            self.wait_for(instance, {instance.AVAILABLE, instance.INSTALLING, instance.READY, instance.FAILED})
            self.settle()
        install.assert_not_called()
        self.assertEqual(instance.state, instance.AVAILABLE, "left for the user to install")
        self.assertIn(MAC_ASSET, self.requested, "the download did finish")
        self.assertFalse(workdir.exists(), "and was thrown away")
        self.assertEqual(instance.controller.settings.get("update_attempt_count", 0), 0, "no attempt counted")

    def background_check(self, base: str, **settings):
        instance = self.make_updater(f"{base}/latest", **settings)
        instance.window_visible = lambda: False
        install = mock.patch.object(instance, "_install_mac").start()
        self.addCleanup(mock.patch.stopall)
        with mock.patch.dict(os.environ, {updater.URL_OVERRIDE_ENV: f"{base}/latest"}):
            instance.check(user_initiated=False)
            # An automatic install leaves AVAILABLE at once, in the same step.
            self.wait_for(instance, {instance.AVAILABLE, instance.INSTALLING, instance.FAILED, instance.IDLE})
            self.settle()
        return instance, install

    def test_checking_and_installing_automatically_are_separate(self) -> None:
        base = self.matching_files()
        instance, install = self.background_check(base, auto_check=False, auto_update=True)
        self.assertEqual((instance.state, self.requested), (instance.IDLE, []), "no background checks")

        instance, install = self.background_check(base, auto_check=True, auto_update=False)
        self.assertEqual(instance.state, instance.AVAILABLE, "found, and left for the user")
        self.assertNotIn(MAC_ASSET, self.requested)

        instance, install = self.background_check(base, auto_check=True, auto_update=True)
        self.assertEqual(instance.state, instance.INSTALLING)
        install.assert_called_once()
        self.assertEqual(instance.controller.settings["update_attempt_version"], "9.9.9")
        self.assertEqual(instance.controller.settings["update_attempt_count"], 1)

    def test_an_old_opt_out_still_stops_background_checks(self) -> None:
        # Before checking and installing were separate, auto_update off meant
        # no checks either; a copy updated from then has no auto_check yet.
        base = self.matching_files()
        instance, _install = self.background_check(base, auto_update=False)
        self.assertEqual((instance.state, self.requested), (instance.IDLE, []), "no background request")
        self.assertFalse(instance.auto_check)

        instance, _install = self.background_check(base, auto_update=False, auto_check=True)
        self.assertEqual(instance.state, instance.AVAILABLE, "checking was turned back on by itself")

    def test_a_download_that_never_matches_is_given_up_on(self) -> None:
        base, _files = self.release_files(checksum="0" * 64)
        instance = self.make_updater(f"{base}/latest", auto_update=True)
        instance.window_visible = lambda: False
        with mock.patch.dict(os.environ, {updater.URL_OVERRIDE_ENV: f"{base}/latest"}):
            for attempt in range(1, updater.GIVE_UP_AFTER + 1):
                instance.check(user_initiated=False)
                self.wait_for(instance, {instance.FAILED})
                self.assertIn("checksum", instance.message)
                self.assertEqual(instance.controller.settings["update_attempt_count"], attempt)
            instance.check(user_initiated=False)
            self.wait_for(instance, {instance.AVAILABLE, instance.FAILED})
            self.settle()
        self.assertEqual(instance.state, instance.AVAILABLE, "found, and left for the user")
        self.assertEqual(self.requested.count(MAC_ASSET), updater.GIVE_UP_AFTER)

    def test_a_version_that_failed_twice_is_not_retried_unattended(self) -> None:
        base = self.matching_files()
        instance, install = self.background_check(
            base, auto_update=True, update_attempt_version="9.9.9", update_attempt_count=1
        )
        install.assert_called_once()
        self.assertEqual(instance.controller.settings["update_attempt_count"], 2)

        instance, install = self.background_check(
            base, auto_update=True, update_attempt_version="9.9.9", update_attempt_count=2
        )
        self.assertEqual(instance.state, instance.AVAILABLE)
        install.assert_not_called()
        instance.install()  # the user can still ask for it
        self.wait_for(instance, {instance.INSTALLING, instance.FAILED})
        install.assert_called_once()

        # A newer version starts afresh.
        instance, install = self.background_check(
            base, auto_update=True, update_attempt_version="9.9.8", update_attempt_count=5
        )
        install.assert_called_once()

    def test_settings_switches(self) -> None:
        instance = self.make_updater("http://127.0.0.1:9/latest")
        self.assertTrue(instance.auto_check, "on unless turned off")
        self.assertTrue(instance.auto_install, "on unless turned off")
        instance.set_auto_check(False)
        self.assertFalse(instance.auto_check)
        self.assertFalse(instance.controller.settings["auto_check"])
        instance.set_auto_install(False)
        self.assertFalse(instance.auto_install)
        instance.set_auto_check(True)
        self.assertTrue(instance.auto_check, "each switch is its own")
        self.assertFalse(instance.auto_install)


class AssetStateTests(unittest.TestCase):
    def test_assets_still_uploading_are_ignored(self) -> None:
        data = release_json()
        data["assets"][0]["state"] = "open"  # the mac zip is still uploading
        self.assertIsNone(release_from_json(data, "mac"))
        data["assets"][0]["state"] = "uploaded"
        self.assertIsNotNone(release_from_json(data, "mac"))


class WindowsScriptTests(unittest.TestCase):
    def test_a_failed_install_reopens_the_old_copy(self) -> None:
        script = updater.windows_installer_script("/RELAUNCH=2", ["--updated", "--minimized"])
        self.assertIn("/VERYSILENT", script)
        self.assertIn("/RELAUNCH=2", script)
        self.assertIn('"%DCF_SRC%" /VERYSILENT', script)
        failure_line = next(line for line in script.splitlines() if line.startswith("if errorlevel 1"))
        self.assertIn('start ""', failure_line)
        self.assertIn('"%DCF_APP%" --updated --minimized', failure_line)

    def test_scripts_hold_no_paths_and_are_plain_ascii(self) -> None:
        for script in (
            updater.windows_installer_script("/RELAUNCH=1", ["--updated"]),
            updater.windows_portable_script(1234, ["--updated", "--minimized"]),
        ):
            script.encode("ascii")
            self.assertNotIn(":\\", script)
            self.assertIn("%DCF_APP%", script)
            self.assertIn("%DCF_SRC%", script)
        portable = updater.windows_portable_script(1234, ["--updated"])
        self.assertIn('move /Y "%DCF_SRC%" "%DCF_APP%"', portable)
        self.assertIn('start "" "%DCF_APP%" --updated', portable)

    def test_paths_go_through_the_environment(self) -> None:
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        app = Path(r"C:\Users\Łukasz 张伟\AppData\Local\Programs\DoubleClick Fixer\DoubleClickFixer.exe")
        setup = Path(r"C:\Users\Łukasz 张伟\AppData\Local\Temp\dcf-update-1\DoubleClickFixer-Setup.exe")
        script = folder / "apply-update.cmd"
        with mock.patch.object(updater.subprocess, "Popen") as popen:
            updater.start_windows_script(script, updater.windows_installer_script("/RELAUNCH=2", []), app, setup)
        self.assertEqual(popen.call_args.args[0], ["cmd", "/c", str(script)])
        environment = popen.call_args.kwargs["env"]
        self.assertEqual((environment["DCF_APP"], environment["DCF_SRC"]), (str(app), str(setup)))
        script.read_bytes().decode("ascii")

        with mock.patch.object(updater.subprocess, "Popen") as popen, self.assertRaises(updater.UpdateError):
            updater.start_windows_script(script, "echo Łukasz", app, setup)
        popen.assert_not_called()


@unittest.skipUnless(sys.platform == "win32", "runs cmd.exe")
class WindowsPortableSwapTests(unittest.TestCase):
    def test_swap_works_in_a_folder_outside_the_oem_code_page(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="dcf-Łł张-"))
        self.addCleanup(shutil.rmtree, root, True)
        marker = root / "relaunched.txt"
        # The "app" is a batch file that notes how it was started.
        app = root / "DoubleClick Fixer.cmd"
        app.write_text('echo old> "%~dp0relaunched.txt"\nexit\n', encoding="ascii")
        downloaded = root / "dcf-update-1" / "new.cmd"
        downloaded.parent.mkdir()
        downloaded.write_text('echo new %*> "%~dp0relaunched.txt"\nexit\n', encoding="ascii")
        finished = subprocess.Popen(["cmd", "/c", "exit"])
        finished.wait()
        script = downloaded.parent / "apply-update.cmd"
        text = updater.windows_portable_script(finished.pid, ["--updated"])
        updater.start_windows_script(script, text, app, downloaded)
        deadline = time.time() + 30
        while not marker.exists() and time.time() < deadline:
            time.sleep(0.2)
        time.sleep(0.5)
        self.assertEqual(marker.read_text().strip(), "new --updated", "the new copy is moved in and started")
        self.assertFalse(downloaded.exists())
        self.assertFalse(script.exists(), "the script removes itself")


class StoredSettingsTests(unittest.TestCase):
    """The update settings as the real AppController reads and writes them."""

    def controller(self, stored: dict):
        from app.controller import AppController

        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        for patch in (
            mock.patch("app.settings.config_dir", return_value=folder),
            mock.patch("app.settings.LEGACY_PATH", folder / "legacy.json"),
            mock.patch("app.startup.is_supported", return_value=False),  # leave the real login item alone
        ):
            patch.start()
            self.addCleanup(patch.stop)
        (folder / "settings.json").write_text(json.dumps(stored))
        return AppController()

    def updater_for(self, controller):
        from app.updater import Updater

        instance = Updater(controller)
        self.addCleanup(instance.deleteLater)
        instance.kind = "mac"
        instance._network = mock.Mock()
        return instance

    def test_an_opt_out_saved_before_the_split_stops_background_checks(self) -> None:
        instance = self.updater_for(self.controller({"auto_update": False}))
        instance.check(user_initiated=False)
        instance._network.get.assert_not_called()
        self.assertEqual(instance.state, instance.IDLE)
        instance.check(user_initiated=True)  # asking still works
        instance._network.get.assert_called_once()

    def test_the_updaters_settings_are_saved_with_the_others(self) -> None:
        controller = self.controller({"auto_update": True, "threshold_ms": 70})
        instance = self.updater_for(controller)
        instance.set_auto_check(False)
        instance.release = updater.Release("9.9.9", "", MAC_ASSET, "", "", "")
        instance._count_attempt()
        saved = json.loads(settings_store.settings_path().read_text())
        self.assertEqual(
            {key: saved.get(key) for key in ("auto_check", "update_attempt_version", "update_attempt_count")},
            {"auto_check": False, "update_attempt_version": "9.9.9", "update_attempt_count": 1},
        )
        self.assertEqual(saved["threshold_ms"], 70)

    def test_the_controllers_own_method_is_used_when_it_has_one(self) -> None:
        from app.updater import Updater

        controller = FakeController()
        controller.store_update_state = mock.Mock()
        instance = Updater(controller)
        self.addCleanup(instance.deleteLater)
        instance.set_auto_check(False)
        controller.store_update_state.assert_called_once_with(auto_check=False)


class RelaunchNoticeTests(unittest.TestCase):
    def test_result_is_reported_once(self) -> None:
        from app.controller import AppController

        with tempfile.TemporaryDirectory() as folder, \
                mock.patch("app.settings.config_dir", return_value=Path(folder)), \
                mock.patch("app.settings.LEGACY_PATH", Path(folder) / "legacy.json"):
            controller = AppController()
            controller.set_pending_update("9.9.9")
            self.assertEqual(controller.take_update_result("9.9.9"), "updated")
            self.assertEqual(controller.take_update_result("9.9.9"), "")
            controller.set_pending_update("9.9.9")
            self.assertEqual(controller.take_update_result("0.2.8"), "failed")

    def test_a_successful_update_clears_the_attempt_count(self) -> None:
        from app.updater import Updater

        controller = FakeController(update_attempt_version="9.9.9", update_attempt_count=1)
        instance = Updater(controller)
        self.addCleanup(instance.deleteLater)
        instance.note_relaunch("failed")
        self.assertEqual(controller.settings["update_attempt_count"], 1)
        instance.note_relaunch("updated")
        self.assertEqual(controller.settings["update_attempt_version"], "")
        self.assertEqual(controller.settings["update_attempt_count"], 0)


if __name__ == "__main__":
    unittest.main()
