"""tools/sign_release.py: key files, release folders, and minisign compatibility."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import base64
import hashlib
import io
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from app.update_signature import parse_claim, parse_public_key, verify
from tools import sign_release
from tools.sign_release import (
    CHECKSUMS,
    SIGNATURE,
    ReleaseError,
    keygen,
    read_secret_key,
    scrypt_parameters,
    sign_release as sign_folder,
    verify_release,
)


def app_zip(version: str, apps: tuple = ("DoubleClick Fixer.app",)) -> bytes:
    """A stand-in for DoubleClickFixer-macos.zip whose apps are `version`."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = plistlib.dumps({"CFBundleShortVersionString": version})
        for app in apps:
            archive.writestr(f"{app}/Contents/Info.plist", info)
            archive.writestr(f"{app}/Contents/MacOS/DoubleClickFixer", b"binary")
        if not apps:
            archive.writestr("README.txt", b"no app here")
    return buffer.getvalue()


class KeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_keygen_makes_a_usable_private_pair_and_never_replaces_one(self) -> None:
        secret, public = keygen("primary", self.root / "secret", self.root / "public")
        key_id, seed = read_secret_key(secret)
        key = parse_public_key(public.read_text())
        self.assertEqual(key.key_id, key_id)
        self.assertEqual(key.key, sign_release.public_key_of(seed))
        if sys.platform != "win32":
            self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
            self.assertEqual(secret.parent.stat().st_mode & 0o777, 0o700)
        before = secret.read_bytes()
        with self.assertRaisesRegex(ReleaseError, "never replaced"):
            keygen("primary", self.root / "secret", self.root / "public")
        self.assertEqual(secret.read_bytes(), before)
        with self.assertRaises(ReleaseError):
            keygen("../escape", self.root / "secret", self.root / "public")

    def test_a_damaged_key_is_refused(self) -> None:
        secret, _public = keygen("k", self.root / "secret", self.root / "public")
        lines = secret.read_text().splitlines()
        body = bytearray(base64.b64decode(lines[1]))
        body[70] ^= 1  # inside the seed
        secret.write_text(f"{lines[0]}\n{base64.b64encode(bytes(body)).decode()}\n")
        with self.assertRaisesRegex(ReleaseError, "damaged"):
            read_secret_key(secret)
        # `minisign -G -W` leaves the checksum out; the public half still
        # gives the damage away.
        body[126:] = bytes(32)
        secret.write_text(f"{lines[0]}\n{base64.b64encode(bytes(body)).decode()}\n")
        with self.assertRaisesRegex(ReleaseError, "damaged"):
            read_secret_key(secret)

    def test_scrypt_parameters_match_libsodium(self) -> None:
        # minisign's own limits (the "sensitive" ones), and libsodium's
        # interactive and minimum ones.
        self.assertEqual(scrypt_parameters(33554432, 1073741824), (1 << 20, 8, 1))
        self.assertEqual(scrypt_parameters(524288, 16777216), (1 << 14, 8, 1))
        self.assertEqual(scrypt_parameters(32768, 16777216), (1 << 10, 8, 1))

    def test_an_encrypted_key_needs_its_password(self) -> None:
        # The layout `minisign -C` writes, with small scrypt limits to keep the
        # test fast.
        seed, key_id = os.urandom(32), os.urandom(8)
        secret = seed + sign_release.public_key_of(seed)
        sealed = key_id + secret + hashlib.blake2b(b"Ed" + key_id + secret, digest_size=32).digest()
        salt, opslimit, memlimit = os.urandom(32), 32768, 16777216
        stream = hashlib.scrypt(b"hunter2", salt=salt, n=1 << 10, r=8, p=1, dklen=len(sealed))
        body = (
            b"Ed" + b"Sc" + b"B2" + salt + opslimit.to_bytes(8, "little") + memlimit.to_bytes(8, "little")
            + bytes(a ^ b for a, b in zip(sealed, stream))
        )
        path = self.root / "encrypted.key"
        path.write_text(f"untrusted comment: minisign encrypted secret key\n{base64.b64encode(body).decode()}\n")
        self.assertEqual(read_secret_key(path, lambda: "hunter2"), (key_id, seed))
        with self.assertRaisesRegex(ReleaseError, "Wrong password"):
            read_secret_key(path, lambda: "hunter3")
        without_scrypt = types.SimpleNamespace(sha512=hashlib.sha512, blake2b=hashlib.blake2b)  # macOS's python3
        with mock.patch.object(sign_release, "hashlib", without_scrypt), \
                self.assertRaisesRegex(ReleaseError, "can't decrypt"):
            read_secret_key(path, lambda: "hunter2")


class ReleaseFolderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.secret, public = keygen("test", self.root / "keys", self.root / "public")
        self.keys = [parse_public_key(public.read_text())]
        self.folder = self.root / "release"
        self.folder.mkdir()
        self.add("DoubleClickFixer-macos.zip", app_zip("1.0.1"))
        self.add("DoubleClickFixer-Setup.exe", b"installer")
        self.add("DoubleClickFixer.exe", b"portable")
        self.write_checksums()

    def add(self, name: str, data: bytes) -> None:
        (self.folder / name).write_bytes(data)

    def write_checksums(self) -> None:
        # The format `sha256sum *` writes in release.yml.
        lines = [
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in sorted(self.folder.iterdir())
            if path.name not in (CHECKSUMS, SIGNATURE)
        ]
        (self.folder / CHECKSUMS).write_text("".join(lines))

    def sign(self, version="1.0.1", **options):
        return sign_folder(self.folder, version, self.secret, keys=self.keys, **options)

    def test_a_signed_folder_is_what_the_app_accepts(self) -> None:
        from app.updater import verified_claim

        claim = self.sign("v1.0.1")
        self.assertEqual(claim.comment(), "dcf 1.0.1")
        signature = (self.folder / SIGNATURE).read_bytes()
        self.assertIn(b"\ntrusted comment: dcf 1.0.1\n", signature)
        with mock.patch("app.updater.__version__", "1.0.0"):
            accepted = verified_claim((self.folder / CHECKSUMS).read_bytes(), signature, "1.0.1", self.keys)
        self.assertEqual(accepted, claim)
        self.assertEqual(verify_release(self.folder, "1.0.1", self.keys), claim)

    def test_a_new_signing_identity_is_named_in_the_comment(self) -> None:
        requirement = 'identifier "com.doubleclickfixer.app" and certificate leaf[subject.OU] = ABCDE12345'
        self.sign(requirement=requirement)
        comment = verify((self.folder / CHECKSUMS).read_bytes(), (self.folder / SIGNATURE).read_bytes(), self.keys)
        self.assertEqual(parse_claim(comment).requirement, requirement)
        with self.assertRaisesRegex(ReleaseError, "one line"):
            self.sign(requirement="a\nb")

    def test_files_that_dont_match_the_checksums_are_refused(self) -> None:
        self.add("DoubleClickFixer.exe", b"swapped after hashing")
        self.add("notes.txt", b"not in the list")
        (self.folder / "DoubleClickFixer-Setup.exe").unlink()
        with self.assertRaises(ReleaseError) as caught:
            self.sign()
        message = str(caught.exception)
        self.assertIn("DoubleClickFixer.exe doesn't match its checksum", message)
        self.assertIn("notes.txt isn't in SHA256SUMS.txt", message)
        self.assertIn("DoubleClickFixer-Setup.exe is in SHA256SUMS.txt but not in the folder", message)
        self.assertFalse((self.folder / SIGNATURE).exists())

    def test_the_mac_app_must_be_the_version_being_signed(self) -> None:
        with self.assertRaisesRegex(ReleaseError, "version 1.0.1, not 1.0.2"):
            self.sign("1.0.2")
        for version in ("1.0.1-beta", "latest", ""):
            with self.assertRaisesRegex(ReleaseError, "isn't a release version"):
                self.sign(version)

    def test_the_mac_zip_must_hold_exactly_one_app(self) -> None:
        # The updater refuses any other number, so the release would never install.
        for apps, message in (
            ((), "There's no app in DoubleClickFixer-macos.zip"),
            (("DoubleClick Fixer.app", "Other.app"), "There are 2 apps in DoubleClickFixer-macos.zip"),
        ):
            self.add("DoubleClickFixer-macos.zip", app_zip("1.0.1", apps))
            self.write_checksums()
            with self.assertRaisesRegex(ReleaseError, message):
                self.sign()
            self.assertFalse((self.folder / SIGNATURE).exists())

    def test_an_app_without_a_version_or_a_broken_zip_is_refused(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("DoubleClick Fixer.app/Contents/MacOS/DoubleClickFixer", b"binary")
        self.add("DoubleClickFixer-macos.zip", buffer.getvalue())
        self.write_checksums()
        with self.assertRaisesRegex(ReleaseError, r"is version \(none\), not 1.0.1"):
            self.sign()
        self.add("DoubleClickFixer-macos.zip", b"not a zip")
        self.write_checksums()
        with self.assertRaisesRegex(ReleaseError, "can't be read as a zip"):
            self.sign()

    def test_a_key_the_app_doesnt_trust_is_refused(self) -> None:
        other, _ = keygen("other", self.root / "keys", self.root / "public")
        with self.assertRaisesRegex(ReleaseError, "wouldn't accept"):
            sign_folder(self.folder, "1.0.1", other, keys=self.keys)
        self.assertFalse((self.folder / SIGNATURE).exists())

    def test_verify_checks_the_files_the_signature_and_its_version(self) -> None:
        self.sign()
        with self.assertRaisesRegex(ReleaseError, "app in DoubleClickFixer-macos.zip"):
            verify_release(self.folder, "1.0.2", self.keys)

        key_id, seed = read_secret_key(self.secret)
        checksums = (self.folder / CHECKSUMS).read_bytes()
        (self.folder / SIGNATURE).write_bytes(sign_release.signature_text(checksums, key_id, seed, "dcf 1.0.0"))
        with self.assertRaisesRegex(ReleaseError, "not version 1.0.1"):
            verify_release(self.folder, "1.0.1", self.keys)

        self.sign()
        (self.folder / "DoubleClickFixer-macos.zip").unlink()
        self.write_checksums()
        with self.assertRaisesRegex(ReleaseError, "doesn't match the file"):
            verify_release(self.folder, "1.0.1", self.keys)

    def test_command_line(self) -> None:
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(sign_release, "trusted_keys", return_value=self.keys), \
                redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(sign_release.main(["sign", "1.0.1", str(self.folder), "--key", str(self.secret)]), 0)
            self.assertEqual(sign_release.main(["verify", "1.0.1", str(self.folder)]), 0)
            self.assertEqual(sign_release.main(["verify", "1.0.2", str(self.folder)]), 1)
        self.assertIn("dcf 1.0.1", output.getvalue())
        self.assertIn("error:", errors.getvalue())

    def test_signatures_are_checked_against_the_keys_built_into_the_app(self) -> None:
        from app.updater import RELEASE_KEYS

        built_in = [parse_public_key(text) for text in RELEASE_KEYS]
        self.assertEqual(sign_release.trusted_keys(), built_in)
        self.assertEqual(len(built_in), 2)
        # tools/keys/ holds copies for the minisign tool, and only those.
        published = [parse_public_key(path.read_text()) for path in sign_release.PUBLIC_KEY_DIR.glob("*.pub")]
        self.assertEqual(sorted(key.key_id_text for key in published), sorted(key.key_id_text for key in built_in))

    def test_a_new_key_isnt_trusted_until_it_is_built_into_the_app(self) -> None:
        # keygen publishes the key next to the others, but installed copies
        # wouldn't accept it, so neither does the tool.
        secret, public = keygen("third", self.root / "keys", self.root / "public")
        with self.assertRaisesRegex(ReleaseError, "wouldn't accept"):
            sign_folder(self.folder, "1.0.1", secret)
        self.assertFalse((self.folder / SIGNATURE).exists())

        source = self.root / "updater.py"
        key_line = public.read_text().splitlines()[1]
        source.write_text(f'"""An updater."""\n\nRELEASE_KEYS = (\n    "{key_line}",  # third\n)\n')
        self.assertEqual(sign_release.trusted_keys(source), [parse_public_key(key_line)])
        with mock.patch.object(sign_release, "UPDATER_SOURCE", source):
            self.assertEqual(sign_folder(self.folder, "1.0.1", secret).comment(), "dcf 1.0.1")

    def test_a_key_list_that_cant_be_read_is_an_error(self) -> None:
        source = self.root / "updater.py"
        for text in ("OTHER = 1\n", "RELEASE_KEYS = tuple(KEYS)\n", "RELEASE_KEYS = ('not a key',)\n", "RELEASE_KEYS = (\n"):
            source.write_text(text)
            with self.assertRaises(ReleaseError, msg=text):
                sign_release.trusted_keys(source)
        with self.assertRaisesRegex(ReleaseError, "can't be read"):
            sign_release.trusted_keys(self.root / "missing.py")


@unittest.skipUnless(shutil.which("minisign"), "minisign isn't installed")
class MinisignCrossCheckTests(unittest.TestCase):
    """The real minisign reads what this tool writes, and the other way round."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.secret, self.public = keygen("cross", self.root / "keys", self.root / "public")
        self.file = self.root / CHECKSUMS
        self.file.write_bytes(f"{'c' * 64}  DoubleClickFixer.exe\n".encode())

    def minisign(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(["minisign", *arguments], capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def test_minisign_verifies_our_signature(self) -> None:
        key_id, seed = read_secret_key(self.secret)
        signature = self.root / SIGNATURE
        signature.write_bytes(sign_release.signature_text(self.file.read_bytes(), key_id, seed, "dcf 1.0.1"))
        result = self.minisign("-V", "-p", str(self.public), "-x", str(signature), "-m", str(self.file))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dcf 1.0.1", result.stdout)

    def test_we_verify_minisigns_signature_made_with_our_key(self) -> None:
        key = parse_public_key(self.public.read_text())
        for legacy in (False, True):
            signature = self.root / f"{legacy}.minisig"
            flags = ["-l"] if legacy else []
            result = self.minisign(
                "-S", *flags, "-W", "-s", str(self.secret), "-t", "dcf 1.0.1",
                "-x", str(signature), "-m", str(self.file),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(verify(self.file.read_bytes(), signature.read_bytes(), [key]), "dcf 1.0.1")


if __name__ == "__main__":
    unittest.main()
