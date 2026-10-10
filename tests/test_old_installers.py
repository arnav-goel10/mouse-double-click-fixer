"""The old installers CI upgrades from: fetched once, cached, and checked
against their SHA-256 whether they came from the cache or from a download
(tools/old_installers.py). The cache must not make the install test any
weaker, and a warm run must not download anything."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import hashlib
import io
import json
import re
import shutil
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from tools import old_installers
from tools.old_installers import INSTALLER, FetchError, Installer, fetch, load, sha256_of

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_URL = "https://github.com/arnav-goel10/mouse-double-click-fixer/releases/download"


def installer_for(version: str, body: bytes) -> Installer:
    return Installer(version, f"{REPOSITORY_URL}/v{version}/{INSTALLER}", hashlib.sha256(body).hexdigest())


class ManifestTests(unittest.TestCase):
    def test_it_lists_the_two_installers_the_install_test_upgrades_from(self) -> None:
        installers = load()
        self.assertEqual([installer.version for installer in installers], ["0.2.6", "0.5.3"])
        for installer in installers:
            self.assertEqual(installer.url, f"{REPOSITORY_URL}/v{installer.version}/{INSTALLER}")
            self.assertRegex(installer.sha256, r"^[0-9a-f]{64}$")

    def test_the_manifest_is_what_the_cache_key_hashes(self) -> None:
        # The key is hashFiles('tools/old_installers.json'): every URL and
        # SHA-256 must be in that file and nowhere else, or changing one
        # wouldn't start the cache over.
        text = (ROOT / "tools" / "old_installers.json").read_text(encoding="utf-8")
        for entry in json.loads(text):
            self.assertEqual(sorted(entry), ["sha256", "url", "version"])
            self.assertIn(entry["url"], text)
            self.assertIn(entry["sha256"], text)
        script = (ROOT / "tools" / "old_installers.py").read_text(encoding="utf-8")
        for installer in load():
            self.assertNotIn(installer.sha256, script)
            self.assertNotIn(installer.url, script)

    def test_a_manifest_with_a_plain_http_url_or_a_short_checksum_is_refused(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        good = {"version": "0.2.6", "url": "https://example.test/a.exe", "sha256": "ab" * 32}
        for name, entry in (("http", {**good, "url": "http://example.test/a.exe"}), ("short", {**good, "sha256": "ab" * 31}),
                            ("upper", {**good, "sha256": "AB" * 32})):
            with self.subTest(name):
                manifest = root / f"{name}.json"
                manifest.write_text(json.dumps([entry]), encoding="utf-8")
                with self.assertRaises(FetchError):
                    load(manifest)


class FetchTests(unittest.TestCase):
    BODIES = {"0.2.6": b"the oldest installer", "0.5.3": b"the last one-file installer"}
    #: Bodies the stand-in release page serves instead of BODIES', by version.
    served: dict = {}

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp()) / "old"
        self.addCleanup(shutil.rmtree, self.folder.parent, True)
        self.installers = [installer_for(version, body) for version, body in self.BODIES.items()]
        self.downloads: list = []
        self.said: list = []

    def get(self, url: str, target: Path) -> None:
        self.downloads.append(url)
        version = re.search(r"/v([\d.]+)/", url).group(1)
        target.write_bytes(self.served.get(version, self.BODIES[version]))

    def run_fetch(self):
        return fetch(self.folder, self.installers, get=self.get, say=self.said.append)

    def test_a_cold_cache_downloads_each_installer_once_and_verifies_it(self) -> None:
        paths = self.run_fetch()
        self.assertEqual(self.downloads, [installer.url for installer in self.installers])
        self.assertEqual(paths, [self.folder / "0.2.6" / INSTALLER, self.folder / "0.5.3" / INSTALLER])
        for path, installer in zip(paths, self.installers):
            self.assertEqual(sha256_of(path), installer.sha256)
        self.assertEqual(sorted(path.name for path in self.folder.rglob("*") if path.is_file()), [INSTALLER, INSTALLER])
        self.assertTrue(all("downloaded, SHA-256" in line for line in self.said[1::2]), self.said)

    def test_a_warm_cache_downloads_nothing_and_still_checks_the_checksums(self) -> None:
        self.run_fetch()
        self.downloads.clear()
        self.said.clear()
        with mock.patch.object(old_installers, "sha256_of", wraps=old_installers.sha256_of) as checked:
            self.assertEqual(self.run_fetch(), [self.folder / "0.2.6" / INSTALLER, self.folder / "0.5.3" / INSTALLER])
        self.assertEqual(self.downloads, [], "a cache hit must not touch the release page")
        self.assertEqual(checked.call_count, 2, "every cached file is hashed")
        self.assertEqual(len(self.said), 2)
        self.assertTrue(all("cached, SHA-256" in line and "verified" in line for line in self.said), self.said)

    def test_a_cached_file_that_is_not_the_listed_one_is_downloaded_again(self) -> None:
        self.run_fetch()
        (self.folder / "0.5.3" / INSTALLER).write_bytes(b"tampered with")
        self.downloads.clear()
        self.run_fetch()
        self.assertEqual(self.downloads, [self.installers[1].url], "only the bad one")
        self.assertEqual(sha256_of(self.folder / "0.5.3" / INSTALLER), self.installers[1].sha256)

    def test_a_partly_cached_folder_downloads_only_the_missing_installer(self) -> None:
        self.run_fetch()
        (self.folder / "0.2.6" / INSTALLER).unlink()
        self.downloads.clear()
        self.run_fetch()
        self.assertEqual(self.downloads, [self.installers[0].url])

    def test_a_download_with_the_wrong_checksum_fails_and_leaves_nothing_to_cache(self) -> None:
        self.served = {"0.2.6": b"not what was released"}
        with self.assertRaises(FetchError) as caught:
            self.run_fetch()
        self.assertIn(self.installers[0].sha256, str(caught.exception))
        self.assertIn(hashlib.sha256(b"not what was released").hexdigest(), str(caught.exception))
        self.assertEqual([path for path in self.folder.rglob("*") if path.is_file()], [], "nothing is left for actions/cache to save")
        self.assertEqual(self.downloads, [self.installers[0].url], "it stops at the first bad one")

    def test_an_interrupted_download_leaves_no_partial_file(self) -> None:
        def broken(url, target):
            target.write_bytes(b"half")
            raise FetchError("connection reset")

        with self.assertRaises(FetchError):
            fetch(self.folder, self.installers, get=broken, say=self.said.append)
        self.assertEqual([path for path in self.folder.rglob("*") if path.is_file()], [])


class DownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = Path(tempfile.mkdtemp()) / "setup.exe"
        self.addCleanup(shutil.rmtree, self.target.parent, True)

    def test_it_retries_a_failed_download_and_then_gives_up(self) -> None:
        waits: list = []
        calls = [urllib.error.URLError("reset"), io.BytesIO(b"the installer")]

        def urlopen(request, timeout):
            result = calls.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with mock.patch.object(old_installers.urllib.request, "urlopen", urlopen):
            old_installers.download("https://example.test/a.exe", self.target, wait=waits.append)
        self.assertEqual(self.target.read_bytes(), b"the installer")
        self.assertEqual(waits, [old_installers.RETRY_WAIT_S])

        attempts = []

        def always_fails(request, timeout):
            attempts.append(request.full_url)
            raise urllib.error.URLError("down")

        waits.clear()
        with mock.patch.object(old_installers.urllib.request, "urlopen", always_fails):
            with self.assertRaises(FetchError):
                old_installers.download("https://example.test/a.exe", self.target, wait=waits.append)
        self.assertEqual(len(attempts), old_installers.ATTEMPTS)
        self.assertEqual(len(waits), old_installers.ATTEMPTS - 1)


class CommandLineTests(unittest.TestCase):
    def test_main_fetches_into_the_folder_and_reports_a_failure_as_exit_status_1(self) -> None:
        folder = Path(tempfile.mkdtemp()) / "old"
        self.addCleanup(shutil.rmtree, folder.parent, True)
        with mock.patch.object(old_installers, "fetch") as fetching:
            self.assertEqual(old_installers.main(["fetch", str(folder)]), 0)
        fetching.assert_called_once_with(folder, load())
        with mock.patch.object(old_installers, "fetch", side_effect=FetchError("bad")):
            self.assertEqual(old_installers.main(["fetch", str(folder)]), 1)


if __name__ == "__main__":
    unittest.main()
