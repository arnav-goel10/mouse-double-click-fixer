import http.server
import json
import os
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

from app import updater
from app.updater import (
    CHECKSUM_ASSET,
    MAC_ASSET,
    WINDOWS_INSTALLER_ASSET,
    is_newer,
    mac_swap_script,
    parse_checksums,
    parse_version,
    release_from_json,
    requirement_is_stable,
    sha256_of,
)


def release_json(version="0.3.0", assets=(MAC_ASSET, WINDOWS_INSTALLER_ASSET, CHECKSUM_ASSET), base="https://x"):
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
        windows = release_from_json(release_json(), "windows-installed")
        self.assertEqual(windows.asset_name, WINDOWS_INSTALLER_ASSET)

    def test_a_release_without_checksums_is_ignored(self) -> None:
        self.assertIsNone(release_from_json(release_json(assets=(MAC_ASSET,)), "mac"))

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


@unittest.skipIf(os.environ.get("QT_QPA_PLATFORM") != "offscreen", "needs offscreen Qt")
class NetworkTests(unittest.TestCase):
    """The real updater against a local stand-in for GitHub."""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def serve(self, files: dict[str, bytes]) -> str:
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
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
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def make_updater(self, url: str):
        from app.updater import Updater

        controller = mock.Mock()
        controller.settings = {"auto_update": False}
        controller.suspended = False
        with mock.patch.dict(os.environ, {updater.URL_OVERRIDE_ENV: url}):
            instance = Updater(controller)
            instance.kind = "mac"
        self.addCleanup(instance.deleteLater)
        return instance

    def wait_for(self, instance, states, timeout=10.0) -> None:
        deadline = time.time() + timeout
        while instance.state not in states and time.time() < deadline:
            self.application.processEvents()
            time.sleep(0.01)
        self.assertIn(instance.state, states, instance.message)

    def test_finds_a_newer_release(self) -> None:
        files: dict[str, bytes] = {}
        base = self.serve(files)
        files["latest"] = json.dumps(release_json("9.9.9", base=base)).encode()
        instance = self.make_updater(f"{base}/latest")
        with mock.patch.dict(os.environ, {updater.URL_OVERRIDE_ENV: f"{base}/latest"}):
            instance.check(user_initiated=True)
            self.wait_for(instance, {instance.AVAILABLE, instance.FAILED})
        self.assertEqual(instance.state, instance.AVAILABLE)
        self.assertEqual(instance.release.version, "9.9.9")

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
        files: dict[str, bytes] = {MAC_ASSET: b"pretend archive"}
        base = self.serve(files)
        files["latest"] = json.dumps(release_json("9.9.9", base=base)).encode()
        files[CHECKSUM_ASSET] = f"{'0' * 64}  {MAC_ASSET}\n".encode()  # does not match
        instance = self.make_updater(f"{base}/latest")
        with mock.patch.dict(os.environ, {updater.URL_OVERRIDE_ENV: f"{base}/latest"}):
            instance.check(user_initiated=True)
            self.wait_for(instance, {instance.AVAILABLE})
            instance.install()
            self.wait_for(instance, {instance.FAILED, instance.INSTALLING})
        self.assertEqual(instance.state, instance.FAILED)
        self.assertIn("checksum", instance.message)

    def test_download_that_matches_reaches_install(self) -> None:
        payload = b"pretend archive"
        files: dict[str, bytes] = {MAC_ASSET: payload}
        base = self.serve(files)
        files["latest"] = json.dumps(release_json("9.9.9", base=base)).encode()
        path = Path(tempfile.mkdtemp()) / "p"
        path.write_bytes(payload)
        files[CHECKSUM_ASSET] = f"{sha256_of(path)}  {MAC_ASSET}\n".encode()
        instance = self.make_updater(f"{base}/latest")
        with mock.patch.dict(os.environ, {updater.URL_OVERRIDE_ENV: f"{base}/latest"}), mock.patch.object(
            instance, "_install_mac"
        ) as install:
            instance.check(user_initiated=True)
            self.wait_for(instance, {instance.AVAILABLE})
            instance.install()
            self.wait_for(instance, {instance.INSTALLING, instance.FAILED})
            deadline = time.time() + 2
            while not install.called and time.time() < deadline:
                self.application.processEvents()
        self.assertEqual(instance.state, instance.INSTALLING, instance.message)
        install.assert_called_once()

    def matching_files(self, payload=b"pretend archive"):
        files: dict[str, bytes] = {MAC_ASSET: payload}
        base = self.serve(files)
        files["latest"] = json.dumps(release_json("9.9.9", base=base)).encode()
        path = Path(tempfile.mkdtemp()) / "p"
        path.write_bytes(payload)
        files[CHECKSUM_ASSET] = f"{sha256_of(path)}  {MAC_ASSET}\n".encode()
        return base

    def test_a_full_disk_fails_instead_of_hanging(self) -> None:
        base = self.matching_files()
        instance = self.make_updater(f"{base}/latest")
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

        with mock.patch.dict(os.environ, {updater.URL_OVERRIDE_ENV: f"{base}/latest"}), \
                mock.patch("builtins.open", opener):
            instance.check(user_initiated=True)
            self.wait_for(instance, {instance.AVAILABLE})
            instance.install()
            self.wait_for(instance, {instance.FAILED, instance.INSTALLING})
        self.assertEqual(instance.state, instance.FAILED)
        self.assertIn("free space", instance.message)
        self.assertIsNone(instance._reply)

    def test_a_background_update_waits_while_the_window_is_open(self) -> None:
        base = self.matching_files()
        instance = self.make_updater(f"{base}/latest")
        instance.window_visible = lambda: True
        with mock.patch.dict(os.environ, {updater.URL_OVERRIDE_ENV: f"{base}/latest"}), \
                mock.patch.object(instance, "_install_mac") as install:
            instance.check(user_initiated=True)
            self.wait_for(instance, {instance.AVAILABLE})
            instance.install(unattended=True)
            self.wait_for(instance, {instance.READY, instance.FAILED})
            install.assert_not_called()
            instance.window_visible = lambda: False
            instance.apply_if_ready()  # the window was closed
            install.assert_called_once()
        self.assertEqual(instance.state, instance.INSTALLING)


class AssetStateTests(unittest.TestCase):
    def test_assets_still_uploading_are_ignored(self) -> None:
        data = release_json()
        data["assets"][0]["state"] = "open"  # the mac zip is still uploading
        self.assertIsNone(release_from_json(data, "mac"))
        data["assets"][0]["state"] = "uploaded"
        self.assertIsNotNone(release_from_json(data, "mac"))


class WindowsInstallerScriptTests(unittest.TestCase):
    def test_a_failed_install_reopens_the_old_copy(self) -> None:
        script = updater.windows_installer_script(
            Path(r"C:\Temp\dcf-update-1\DoubleClickFixer-Setup.exe"),
            Path(r"C:\Users\a\AppData\Local\Programs\DoubleClick Fixer\DoubleClickFixer.exe"),
            "/RELAUNCH=2",
            ["--updated", "--minimized"],
        )
        self.assertIn("/VERYSILENT", script)
        self.assertIn("/RELAUNCH=2", script)
        failure_line = next(line for line in script.splitlines() if line.startswith("if errorlevel 1"))
        self.assertIn('start ""', failure_line)
        self.assertIn('DoubleClickFixer.exe" --updated --minimized', failure_line)
        self.assertIn("--updated --minimized", script)


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


if __name__ == "__main__":
    unittest.main()
