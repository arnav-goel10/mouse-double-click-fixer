"""CI's throwaway update key: made, used and refused where it must be."""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from app import updater
from app.update_signature import parse_public_key
from tools import ci_update_key


class CiUpdateKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def test_the_app_and_the_tool_agree_on_the_file_name(self) -> None:
        self.assertEqual(ci_update_key.KEY_FILE, updater.CI_KEY_FILE)

    def test_a_release_signed_with_it_checks_out_against_it(self) -> None:
        public = ci_update_key.make(self.folder / "key")
        self.assertEqual(public.name, updater.CI_KEY_FILE)
        checksums = self.folder / "SHA256SUMS.txt"
        checksums.write_bytes(b"0" * 64 + b"  DoubleClickFixer-Setup.exe\r\n")
        signature = ci_update_key.sign("9.9.9", checksums, public.with_suffix(".key"))
        claim = updater.verified_claim(
            checksums.read_bytes(), signature.read_bytes(), "9.9.9", keys=[parse_public_key(public.read_text())]
        )
        self.assertEqual(claim.version, "9.9.9")
        with self.assertRaises(updater.UpdateError):
            updater.verified_claim(checksums.read_bytes(), signature.read_bytes(), "9.9.9")  # release keys only

    def bundle(self, frozen: bool, with_key: bool, platform: str = "win32"):
        bundled = self.folder / "_internal"
        bundled.mkdir(exist_ok=True)
        if with_key:
            ci_update_key.make(self.folder / "key")
            (bundled / updater.CI_KEY_FILE).write_bytes((self.folder / "key" / updater.CI_KEY_FILE).read_bytes())
        patches = [mock.patch.object(sys, "_MEIPASS", str(bundled), create=True)]
        patches.append(mock.patch.object(sys, "frozen", frozen, create=True))
        patches.append(mock.patch.object(sys, "platform", platform))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_ci_build_trusts_the_key_it_carries(self) -> None:
        self.bundle(frozen=True, with_key=True)
        with self.assertLogs("app.updater", "WARNING"):
            keys = updater.trusted_keys()
        self.assertEqual(len(keys), len(updater.RELEASE_KEYS) + 1)

    def test_a_release_build_trusts_the_release_keys_only(self) -> None:
        self.bundle(frozen=True, with_key=False)
        self.assertEqual(len(updater.trusted_keys()), len(updater.RELEASE_KEYS))

    def test_a_macos_build_never_reads_the_file(self) -> None:
        # Only the Windows end-to-end job uses the key; a Mac app's bundle
        # is user-writable, so nothing there may add a trusted key.
        self.bundle(frozen=True, with_key=True, platform="darwin")
        self.assertEqual(len(updater.trusted_keys()), len(updater.RELEASE_KEYS))

    def test_running_from_source_never_reads_the_file(self) -> None:
        self.bundle(frozen=False, with_key=True)
        self.assertEqual(len(updater.trusted_keys()), len(updater.RELEASE_KEYS))

    def test_an_unreadable_key_file_is_ignored(self) -> None:
        self.bundle(frozen=True, with_key=False)
        (self.folder / "_internal" / updater.CI_KEY_FILE).write_text("not a key")
        with self.assertLogs("app.updater", "WARNING"):
            self.assertEqual(len(updater.trusted_keys()), len(updater.RELEASE_KEYS))

    def check(self, *paths: Path) -> int:
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            return ci_update_key.main(["check", *map(str, paths)])

    def test_check_finds_the_key_in_a_folder_and_in_a_one_file_exe(self) -> None:
        clean = self.folder / "clean"
        (clean / "_internal").mkdir(parents=True)
        (clean / "DoubleClickFixer.exe").write_bytes(b"MZ...PYZ-00.pyz...base_library.zip")
        self.assertEqual(self.check(clean, clean / "DoubleClickFixer.exe"), 0)
        carrying = self.folder / "carrying"
        (carrying / "_internal").mkdir(parents=True)
        (carrying / "_internal" / updater.CI_KEY_FILE).write_text("key")
        self.assertEqual(self.check(carrying), 1)
        exe = self.folder / "onefile.exe"
        exe.write_bytes(b"MZ...\x00dcf-ci-update-key.pub\x00...")
        self.assertEqual(self.check(exe), 1)
        self.assertEqual(self.check(self.folder / "missing.exe"), 2)


if __name__ == "__main__":
    unittest.main()
