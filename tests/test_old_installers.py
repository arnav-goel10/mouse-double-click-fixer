"""The old installers CI upgrades from: kept in a cache and an artifact, and
checked against their SHA-256 whether they came from the cache, the artifact
or a download (tools/old_installers.py). Neither must make the install test
any weaker, and a run that finds them in either must not touch the release
page, whose downloads count in the public numbers."""

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
import zipfile
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from unittest import mock

from tools import old_installers
from tools.old_installers import ARTIFACT, INSTALLER, ArtifactSource, FetchError, Installer, fetch, load, sha256_of

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


NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeOpener:
    """The Actions API: a listing, and a redirect for each archive URL."""

    def __init__(self, artifacts, fail_listing=False) -> None:
        self.artifacts, self.fail_listing = artifacts, fail_listing
        self.requests: list = []

    def open(self, request, timeout):
        self.requests.append(request)
        url = request.full_url
        if "/actions/artifacts?" in url:
            if self.fail_listing:
                raise urllib.error.HTTPError(url, 403, "Resource not accessible by integration", Message(), None)
            return FakeResponse(json.dumps({"artifacts": self.artifacts}).encode())
        headers = Message()
        headers["Location"] = "https://storage.example.test/" + url.rsplit("/", 2)[-2] + ".zip"
        raise urllib.error.HTTPError(url, 302, "Found", headers, None)


class ArtifactTests(unittest.TestCase):
    BODIES = FetchTests.BODIES

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.folder = self.root / "old"
        self.work = self.root / "work"
        self.work.mkdir()
        self.installers = [installer_for(version, body) for version, body in self.BODIES.items()]
        self.zips: dict = {}
        self.storage_gets: list = []
        self.release_gets: list = []
        self.said: list = []

    def artifact(self, number: int, members: dict, created: datetime, expires: datetime, expired=False) -> dict:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, body in members.items():
                archive.writestr(name, body)
        self.zips[f"https://storage.example.test/{number}.zip"] = buffer.getvalue()
        return {"id": number, "name": ARTIFACT, "expired": expired, "created_at": created.isoformat(),
                "expires_at": expires.isoformat().replace("+00:00", "Z"),
                "archive_download_url": f"https://api.github.test/repos/o/r/actions/artifacts/{number}/zip"}

    def full(self) -> dict:
        return {f"{version}/{INSTALLER}": body for version, body in self.BODIES.items()}

    def storage_get(self, url: str, target: Path) -> None:
        self.storage_gets.append(url)
        target.write_bytes(self.zips[url])

    def release_get(self, url: str, target: Path) -> None:
        self.release_gets.append(url)
        version = re.search(r"/v([\d.]+)/", url).group(1)
        target.write_bytes(self.BODIES[version])

    def source(self, opener) -> ArtifactSource:
        source = ArtifactSource("o/r", "token", self.work, now=lambda: NOW, opener=opener, get=self.storage_get,
                                say=self.said.append)
        self.addCleanup(source.close)
        return source

    def run_fetch(self, source):
        return fetch(self.folder, self.installers, get=self.release_get, say=self.said.append, source=source)

    def test_a_cold_cache_takes_both_from_the_newest_artifact_and_never_the_release_page(self) -> None:
        older = self.artifact(1, {}, NOW - timedelta(days=20), NOW + timedelta(days=70))
        newer = self.artifact(2, self.full(), NOW - timedelta(days=5), NOW + timedelta(days=85))
        opener = FakeOpener([older, newer])
        source = self.source(opener)
        paths = self.run_fetch(source)
        self.assertEqual(self.release_gets, [])
        self.assertEqual(self.storage_gets, ["https://storage.example.test/2.zip"], "one zip, the newest")
        for path, installer in zip(paths, self.installers):
            self.assertEqual(sha256_of(path), installer.sha256)
        self.assertFalse(source.needs_refresh())
        storage = [r for r in opener.requests if "storage" in r.full_url]
        self.assertEqual(storage, [], "the token never goes to the storage host")
        self.assertTrue(all(r.get_header("Authorization") == "Bearer token" for r in opener.requests))

    def test_a_warm_cache_downloads_no_zip(self) -> None:
        self.run_fetch(None)
        self.release_gets.clear()
        opener = FakeOpener([self.artifact(2, self.full(), NOW, NOW + timedelta(days=85))])
        self.run_fetch(self.source(opener))
        self.assertEqual(self.storage_gets, [])
        self.assertEqual(self.release_gets, [])

    def test_a_tampered_artifact_falls_back_to_the_release_page_and_asks_for_a_fresh_one(self) -> None:
        members = {**self.full(), f"0.5.3/{INSTALLER}": b"tampered with"}
        source = self.source(FakeOpener([self.artifact(2, members, NOW, NOW + timedelta(days=85))]))
        paths = self.run_fetch(source)
        self.assertEqual(self.release_gets, [self.installers[1].url], "only the bad one")
        self.assertEqual(sha256_of(paths[1]), self.installers[1].sha256)
        self.assertTrue(any("the artifact's file has SHA-256" in line for line in self.said), self.said)
        self.assertEqual([p.name for p in self.folder.rglob("*") if p.is_file()], [INSTALLER, INSTALLER])

    def test_no_artifact_means_the_release_page_and_an_upload(self) -> None:
        expired = self.artifact(1, self.full(), NOW - timedelta(days=100), NOW - timedelta(days=10), expired=True)
        source = self.source(FakeOpener([expired]))
        self.run_fetch(source)
        self.assertEqual(self.release_gets, [installer.url for installer in self.installers])
        self.assertEqual(self.storage_gets, [], "an expired artifact isn't fetched")
        self.assertTrue(source.needs_refresh())

    def test_an_artifact_close_to_expiry_asks_for_a_fresh_one(self) -> None:
        source = self.source(FakeOpener([self.artifact(2, self.full(), NOW, NOW + timedelta(days=10))]))
        self.assertTrue(source.needs_refresh())

    def test_an_unreadable_listing_falls_back_and_asks_for_no_upload(self) -> None:
        source = self.source(FakeOpener([], fail_listing=True))
        self.run_fetch(source)
        self.assertEqual(self.release_gets, [installer.url for installer in self.installers])
        self.assertFalse(source.needs_refresh(), "a run that can't read artifacts mustn't upload on every run")


class CommandLineTests(unittest.TestCase):
    def test_main_fetches_into_the_folder_and_reports_a_failure_as_exit_status_1(self) -> None:
        folder = Path(tempfile.mkdtemp()) / "old"
        self.addCleanup(shutil.rmtree, folder.parent, True)
        clean = {key: value for key, value in old_installers.os.environ.items() if key not in ("GITHUB_TOKEN", "GITHUB_OUTPUT")}
        with mock.patch.dict(old_installers.os.environ, clean, clear=True):
            with mock.patch.object(old_installers, "fetch") as fetching:
                self.assertEqual(old_installers.main(["fetch", str(folder)]), 0)
            fetching.assert_called_once()
            self.assertEqual(fetching.call_args.args, (folder, load()))
            self.assertIsNone(fetching.call_args.kwargs["source"], "no token, no artifact")
            with mock.patch.object(old_installers, "fetch", side_effect=FetchError("bad")):
                self.assertEqual(old_installers.main(["fetch", str(folder)]), 1)

    def test_with_a_token_it_reads_the_artifact_and_sets_the_upload_output(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        output = root / "output"
        env = {"GITHUB_TOKEN": "token", "GITHUB_REPOSITORY": "o/r", "GITHUB_OUTPUT": str(output)}
        with mock.patch.dict(old_installers.os.environ, env), \
                mock.patch.object(old_installers, "fetch") as fetching, \
                mock.patch.object(old_installers.ArtifactSource, "needs_refresh", return_value=False):
            self.assertEqual(old_installers.main(["fetch", str(root / "old")]), 0)
        self.assertIsInstance(fetching.call_args.kwargs["source"], ArtifactSource)
        self.assertEqual(output.read_text(encoding="utf-8"), "upload=false\n")


if __name__ == "__main__":
    unittest.main()
