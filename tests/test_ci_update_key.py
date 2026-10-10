"""CI's throwaway update key: made, used and refused where it must be."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from app import build_flags, updater
from app.update_signature import parse_public_key
from tools import ci_update_key

ROOT = Path(__file__).resolve().parents[1]


class CiUpdateKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def test_a_release_signed_with_it_checks_out_against_it(self) -> None:
        public = ci_update_key.make(self.folder / "key")
        self.assertEqual(public.name, ci_update_key.KEY_FILE)
        checksums = self.folder / "SHA256SUMS.txt"
        checksums.write_bytes(b"0" * 64 + b"  DoubleClickFixer-Setup.exe\r\n")
        signature = ci_update_key.sign("9.9.9", checksums, public.with_suffix(".key"))
        claim = updater.verified_claim(
            checksums.read_bytes(), signature.read_bytes(), "9.9.9", keys=[parse_public_key(public.read_text())]
        )
        self.assertEqual(claim.version, "9.9.9")
        with self.assertRaises(updater.UpdateError):
            updater.verified_claim(checksums.read_bytes(), signature.read_bytes(), "9.9.9")  # release keys only

    def key_line(self) -> str:
        public = ci_update_key.make(self.folder / "key")
        return public.read_text(encoding="ascii").splitlines()[-1]

    def built(self, key: str, frozen: bool = True, platform: str = "win32") -> None:
        """A copy of the app as a build left it: `key` is what its start-up hook set."""
        patches = [
            mock.patch.object(build_flags, "CI_UPDATE_KEY", key),
            mock.patch.object(sys, "frozen", frozen, create=True),
            mock.patch.object(sys, "platform", platform),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_ci_build_trusts_the_key_it_was_built_with(self) -> None:
        self.built(self.key_line())
        with self.assertLogs("app.updater", "WARNING"):
            keys = updater.trusted_keys()
        self.assertEqual(len(keys), len(updater.RELEASE_KEYS) + 1)

    def test_a_release_build_trusts_the_release_keys_only(self) -> None:
        self.built("")
        self.assertEqual(len(updater.trusted_keys()), len(updater.RELEASE_KEYS))

    def test_a_key_file_beside_the_app_is_never_read(self) -> None:
        # The key used to be a file in the bundle, honoured at run time.
        bundled = self.folder / "_internal"
        bundled.mkdir()
        (bundled / ci_update_key.KEY_FILE).write_bytes((ci_update_key.make(self.folder / "key")).read_bytes())
        self.built("")
        with mock.patch.object(sys, "_MEIPASS", str(bundled), create=True):
            self.assertEqual(len(updater.trusted_keys()), len(updater.RELEASE_KEYS))

    def test_only_a_frozen_windows_app_trusts_it(self) -> None:
        line = self.key_line()
        for frozen, platform in ((True, "darwin"), (False, "win32")):
            with self.subTest(frozen=frozen, platform=platform):
                with mock.patch.object(build_flags, "CI_UPDATE_KEY", line), \
                        mock.patch.object(sys, "frozen", frozen, create=True), \
                        mock.patch.object(sys, "platform", platform):
                    self.assertEqual(len(updater.trusted_keys()), len(updater.RELEASE_KEYS))

    def test_a_key_that_cant_be_read_is_ignored(self) -> None:
        self.built("not a key")
        with self.assertLogs("app.updater", "WARNING"):
            self.assertEqual(len(updater.trusted_keys()), len(updater.RELEASE_KEYS))

    def test_the_hook_sets_the_flag_before_the_app_runs(self) -> None:
        line = self.key_line()
        with redirect_stdout(io.StringIO()):
            hooks = ci_update_key.spec_runtime_hooks(
                {ci_update_key.ENV: str(self.folder / "key" / ci_update_key.KEY_FILE)}, "win32", self.folder / "hooks"
            )
        self.assertEqual([Path(hook).name for hook in hooks], [ci_update_key.HOOK_FILE])
        self.addCleanup(setattr, build_flags, "CI_UPDATE_KEY", build_flags.CI_UPDATE_KEY)
        exec(compile(Path(hooks[0]).read_text(encoding="ascii"), hooks[0], "exec"), {})
        self.assertEqual(build_flags.CI_UPDATE_KEY, line)
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "platform", "win32"):
            with self.assertLogs("app.updater", "WARNING"):
                self.assertEqual(updater.trusted_keys()[-1], parse_public_key(line))

    def test_the_spec_refuses_a_variable_that_names_no_key(self) -> None:
        bad = self.folder / "bad.pub"
        bad.write_text("not a key", encoding="ascii")
        for named in (str(bad), str(self.folder / "missing.pub")):
            with self.subTest(named=named), self.assertRaises(SystemExit):
                ci_update_key.spec_runtime_hooks({ci_update_key.ENV: named}, "win32", self.folder / "hooks")
        self.assertFalse((self.folder / "hooks" / ci_update_key.HOOK_FILE).exists())

    def test_a_hook_left_from_another_build_is_removed(self) -> None:
        hooks = self.folder / "hooks"
        hooks.mkdir()
        (hooks / ci_update_key.HOOK_FILE).write_text("stale", encoding="ascii")
        self.assertEqual(ci_update_key.spec_runtime_hooks({}, "win32", hooks), [])
        self.assertFalse((hooks / ci_update_key.HOOK_FILE).exists())

    def test_no_app_source_names_the_marker(self) -> None:
        # `check` looks for it in what a build ships, so nothing the app
        # always ships may contain it.
        for path in sorted((ROOT / "app").rglob("*.py")):
            self.assertNotIn(ci_update_key.MARKER, path.read_bytes(), path.name)

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
        (carrying / "_internal" / ci_update_key.KEY_FILE).write_text("key")
        self.assertEqual(self.check(carrying), 1)
        # The start-up hook, as PyInstaller lists it in an executable's archive
        # (the one-file exe, and the installed folder's exe).
        exe = self.folder / "onefile.exe"
        exe.write_bytes(b"MZ...pyiboot01_bootstrap\x00...dcf-ci-update-key\x00...run\x00")
        self.assertEqual(self.check(exe), 1)
        onedir = self.folder / "onedir" / "DoubleClickFixer"
        (onedir / "_internal").mkdir(parents=True)
        (onedir / "DoubleClickFixer.exe").write_bytes(exe.read_bytes())
        self.assertEqual(self.check(onedir.parent), 1)
        self.assertEqual(self.check(self.folder / "missing.exe"), 2)


if __name__ == "__main__":
    unittest.main()
