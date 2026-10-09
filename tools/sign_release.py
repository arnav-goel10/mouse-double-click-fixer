"""Sign a release with the update key.

An installed copy of DoubleClick Fixer installs an update only when the
release's SHA256SUMS.txt carries a minisign signature from one of the keys
built into the app: RELEASE_KEYS in app/updater.py, which this tool reads too
(tools/keys/ has copies for the minisign tool). The secret keys never go to
GitHub or CI; they stay on the owner's Mac, and releases are signed there.
Keep the release a draft until its signature is uploaded:

    gh release download v1.0.1 --dir ~/dcf-release-1.0.1
    python3 tools/sign_release.py sign 1.0.1 ~/dcf-release-1.0.1
    gh release upload v1.0.1 ~/dcf-release-1.0.1/SHA256SUMS.txt.minisig
    gh release edit v1.0.1 --draft=false

`sign` first checks every file in the folder against SHA256SUMS.txt (all of
them listed, none missing, every hash right) and that the macOS zip holds
exactly one app, of that version. It then signs SHA256SUMS.txt with the
trusted comment "dcf 1.0.1", checks the signature against RELEASE_KEYS and
writes SHA256SUMS.txt.minisig. Upload the signature before taking the release
out of draft: copies of the app that check signatures don't offer a release
without one, and say so.

    --key PATH          sign with another secret key (default: the primary key)
    --requirement DR    for a release that moves the macOS app to a new signing
                        identity: the new designated requirement, exactly as
                        `codesign -d -r- "DoubleClick Fixer.app"` prints it after
                        "designated =>". The signed comment then names it, and
                        installed copies accept the new signature.

Other commands:

    python3 tools/sign_release.py verify 1.0.1 FOLDER
        Check a signed release folder the way the app will.
    python3 tools/sign_release.py keygen NAME
        Make a key pair: the secret key goes to
        ~/.doubleclick-fixer-signing/update-keys/NAME.key, readable only by
        you, and the public key to tools/keys/NAME.pub. It never replaces an
        existing key. Neither the app nor this tool trusts the new key until
        its key line is added to RELEASE_KEYS, and then only app versions
        built with it, so the backup key must be in the app before it is
        needed.

Keep a copy of both secret keys somewhere offline. Losing both means installed
copies can't be updated again; anyone who gets one can sign updates.

The files are minisign's own (https://jedisct1.github.io/minisign/), so the
minisign tool works with them too: `minisign -V -p tools/keys/primary.pub -m
SHA256SUMS.txt` checks a signature, and `minisign -C -s KEY` adds a password to
a secret key, which this tool then asks for. Signatures use minisign's default
prehashed format ("ED": the Ed25519 signature covers the file's BLAKE2b-512
hash). A secret key file is

    untrusted comment: <free text>
    base64("Ed" | KDF: "Sc" or two zero bytes | "B2" | salt: 32 bytes |
           opslimit: 8 bytes | memlimit: 8 bytes | key id: 8 bytes |
           secret key: 64 bytes | checksum: 32 bytes)

where the secret key is the 32-byte Ed25519 seed followed by the public key,
the checksum is BLAKE2b-256 of "Ed" | key id | secret key, and with "Sc" the
last 104 bytes are XORed with scrypt(password, salt) using libsodium's
parameters for opslimit and memlimit. Keys made here are not encrypted (KDF
zero, as `minisign -G -W` makes them), so they rely on the file's permissions.
"""

from __future__ import annotations

import argparse
import ast
import base64
import getpass
import hashlib
import os
import plistlib
import re
import sys
import zipfile
from pathlib import Path
from typing import Callable, Optional
from xml.parsers.expat import ExpatError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.update_signature import (  # noqa: E402
    BASE,
    L,
    LEGACY,
    PREHASHED,
    TRUSTED_PREFIX,
    UNTRUSTED_PREFIX,
    PublicKey,
    ReleaseClaim,
    SignatureError,
    challenge,
    encode_point,
    key_id_text,
    multiply_point,
    parse_claim,
    parse_public_key,
    signed_payload,
    verify,
)

KEY_DIR = Path.home() / ".doubleclick-fixer-signing" / "update-keys"
PUBLIC_KEY_DIR = ROOT / "tools" / "keys"
#: Where the keys the app trusts are listed (RELEASE_KEYS).
UPDATER_SOURCE = ROOT / "app" / "updater.py"
PRIMARY = "primary"
CHECKSUMS = "SHA256SUMS.txt"
SIGNATURE = CHECKSUMS + ".minisig"
#: The macOS update: the updater installs it only if it holds exactly one app.
MAC_ZIP = "DoubleClickFixer-macos.zip"

KDF_NONE = b"\0\0"
KDF_SCRYPT = b"Sc"
CHECKSUM_ALGORITHM = b"B2"


class ReleaseError(RuntimeError):
    pass


# -- Ed25519 signing (RFC 8032 section 5.1.6) -------------------------------------

def _expand(seed: bytes) -> tuple[int, bytes]:
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def public_key_of(seed: bytes) -> bytes:
    return encode_point(multiply_point(_expand(seed)[0], BASE))


def ed25519_sign(seed: bytes, message: bytes) -> bytes:
    scalar, prefix = _expand(seed)
    public = encode_point(multiply_point(scalar, BASE))
    nonce = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % L
    r = encode_point(multiply_point(nonce, BASE))
    s = (nonce + challenge(r, public, message) * scalar) % L
    return r + s.to_bytes(32, "little")


# -- minisign files ---------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _checksum(key_id: bytes, secret: bytes) -> bytes:
    return hashlib.blake2b(LEGACY + key_id + secret, digest_size=32).digest()


def public_key_text(key_id: bytes, public: bytes, name: str) -> str:
    return (
        f"{UNTRUSTED_PREFIX}minisign public key {key_id_text(key_id)}, DoubleClick Fixer {name} update key\n"
        f"{_b64(LEGACY + key_id + public)}\n"
    )


def secret_key_text(key_id: bytes, seed: bytes, name: str) -> str:
    secret = seed + public_key_of(seed)
    body = (
        LEGACY + KDF_NONE + CHECKSUM_ALGORITHM + bytes(32) + bytes(8) + bytes(8)
        + key_id + secret + _checksum(key_id, secret)
    )
    return (
        f"{UNTRUSTED_PREFIX}minisign secret key {key_id_text(key_id)}, DoubleClick Fixer {name} "
        f"update key, not encrypted\n{_b64(body)}\n"
    )


def scrypt_parameters(opslimit: int, memlimit: int) -> tuple[int, int, int]:
    """(N, r, p) for libsodium's crypto_pwhash_scryptsalsa208sha256 limits,
    which is how minisign stores the cost of an encrypted key."""
    opslimit = max(opslimit, 32768)
    r = 8
    if opslimit < memlimit // 32:
        p = 1
        max_n = opslimit // (r * 4)
    else:
        max_n = memlimit // (r * 128)
    log_n = 1
    while log_n < 63 and (1 << log_n) <= max_n // 2:
        log_n += 1
    if opslimit >= memlimit // 32:
        p = min((opslimit // 4) // (1 << log_n), 0x3FFFFFFF) // r
    return 1 << log_n, r, p


def read_secret_key(path: Path, password: Callable[[], str] = lambda: "") -> tuple[bytes, bytes]:
    """(key id, Ed25519 seed) from a minisign secret key file."""
    lines = [line for line in path.read_text(encoding="ascii").splitlines() if line.strip()]
    try:
        body = base64.b64decode(lines[-1], validate=True)
    except (IndexError, ValueError) as error:
        raise ReleaseError(f"{path} isn't a minisign secret key.") from error
    if len(body) != 158 or body[:2] != LEGACY or body[4:6] != CHECKSUM_ALGORITHM:
        raise ReleaseError(f"{path} isn't a minisign Ed25519 secret key.")
    kdf, salt, sealed = body[2:4], body[6:38], body[54:]
    if kdf == KDF_SCRYPT:
        if not hasattr(hashlib, "scrypt"):  # Python built against LibreSSL, like macOS's own
            raise ReleaseError("This Python can't decrypt the key. Run the tool with Homebrew's python3.")
        n, r, p = scrypt_parameters(int.from_bytes(body[38:46], "little"), int.from_bytes(body[46:54], "little"))
        stream = hashlib.scrypt(
            password().encode("utf-8"), salt=salt, n=n, r=r, p=p,
            maxmem=min(128 * r * (n + p + 2), 2**31 - 1), dklen=len(sealed),
        )
        sealed = bytes(a ^ b for a, b in zip(sealed, stream))
    elif kdf != KDF_NONE:
        raise ReleaseError(f"{path} is encrypted in a way this tool can't read.")
    key_id, secret, checksum = sealed[:8], sealed[8:72], sealed[72:]
    # `minisign -G -W` leaves the checksum of an unencrypted key empty; the
    # public half below still catches a damaged file.
    if not (kdf == KDF_NONE and checksum == bytes(32)) and checksum != _checksum(key_id, secret):
        raise ReleaseError("Wrong password." if kdf == KDF_SCRYPT else f"{path} is damaged.")
    if public_key_of(secret[:32]) != secret[32:]:
        raise ReleaseError(f"{path} is damaged.")
    return key_id, secret[:32]


def signature_text(message: bytes, key_id: bytes, seed: bytes, trusted_comment: str) -> bytes:
    """A minisign signature file for `message`, in the prehashed format."""
    if "\n" in trusted_comment or "\r" in trusted_comment:
        raise ReleaseError("The trusted comment must be one line.")
    signature = ed25519_sign(seed, signed_payload(PREHASHED, message))
    global_signature = ed25519_sign(seed, signature + trusted_comment.encode("utf-8"))
    lines = [
        f"{UNTRUSTED_PREFIX}signature from DoubleClick Fixer update key {key_id_text(key_id)}",
        _b64(PREHASHED + key_id + signature),
        TRUSTED_PREFIX + trusted_comment,
        _b64(global_signature),
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def trusted_keys(source: Optional[Path] = None) -> list[PublicKey]:
    """The keys installed copies trust: RELEASE_KEYS in app/updater.py.

    Read from the source rather than imported, because the updater needs
    PySide6 and this tool runs on a plain python3. Keeping no second list
    means the tool can't accept a key that installed copies would refuse.
    """
    source = UPDATER_SOURCE if source is None else source
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), str(source))
    except (OSError, SyntaxError, ValueError) as error:
        raise ReleaseError(f"{source} can't be read: {error}") from error
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "RELEASE_KEYS" for target in node.targets
        ):
            try:
                texts = ast.literal_eval(node.value)
                if not isinstance(texts, (tuple, list)) or not all(isinstance(text, str) for text in texts):
                    raise ValueError("it isn't a list of key lines")
                return [parse_public_key(text) for text in texts]
            except (ValueError, SignatureError) as error:
                raise ReleaseError(f"RELEASE_KEYS in {source} can't be read: {error}") from error
    raise ReleaseError(f"{source} has no RELEASE_KEYS.")


# -- release folders --------------------------------------------------------------

def normalise_version(text: str) -> str:
    version = text.strip().removeprefix("v")
    if not re.fullmatch(r"\d+(?:\.\d+)*", version):
        raise ReleaseError(f"{text!r} isn't a release version like 1.0.1.")
    return version


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _zipped_apps(path: Path) -> dict[str, str]:
    """Each app at the top of a zip, with the version its Info.plist gives
    ("" when it gives none). The updater unpacks the zip and looks for apps
    at the top of it in the same way."""
    with zipfile.ZipFile(path) as archive:
        top = {name.split("/", 1)[0] for name in archive.namelist()}
        apps: dict[str, str] = {}
        for app in sorted(name for name in top if name.endswith(".app")):
            try:
                info = plistlib.loads(archive.read(f"{app}/Contents/Info.plist"))
            except (KeyError, ValueError, ExpatError):  # no Info.plist, or not a plist
                info = {}
            apps[app] = str(info.get("CFBundleShortVersionString", "")) if isinstance(info, dict) else ""
        return apps


def _zip_problems(path: Path, version: str) -> list[str]:
    try:
        apps = _zipped_apps(path)
    except (zipfile.BadZipFile, OSError) as error:
        return [f"{path.name} can't be read as a zip: {error}"]
    problems = []
    if path.name == MAC_ZIP and len(apps) != 1:
        # Installed copies refuse any other number, so the release would
        # never install.
        problems.append(
            f"There's no app in {path.name}." if not apps
            else f"There are {len(apps)} apps in {path.name}; installed copies accept exactly one."
        )
    for found in apps.values():
        if found != version:
            problems.append(f"The app in {path.name} is version {found or '(none)'}, not {version}.")
    return problems


def check_release_folder(folder: Path, version: str) -> bytes:
    """SHA256SUMS.txt's contents, once every file in `folder` matches it."""
    try:
        checksums = (folder / CHECKSUMS).read_bytes()
    except OSError as error:
        raise ReleaseError(f"{folder / CHECKSUMS} can't be read.") from error
    listed: dict[str, str] = {}
    problems: list[str] = []
    for line in checksums.decode("utf-8", errors="replace").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64}) [ *]([^/\\]+)", line.strip())
        if match is None:
            if line.strip():
                problems.append(f"{CHECKSUMS} has a line that isn't a checksum: {line.strip()!r}")
            continue
        listed[match.group(2)] = match.group(1)
    if not listed:
        problems.append(f"{CHECKSUMS} lists no files.")
    for name, digest in listed.items():
        path = folder / name
        if not path.is_file():
            problems.append(f"{name} is in {CHECKSUMS} but not in the folder.")
        elif _sha256(path) != digest:
            problems.append(f"{name} doesn't match its checksum.")
        elif name.endswith(".zip"):
            problems.extend(_zip_problems(path, version))
    for path in sorted(folder.iterdir()):
        if path.name not in listed and path.name not in (CHECKSUMS, SIGNATURE) and not path.name.startswith("."):
            problems.append(f"{path.name} isn't in {CHECKSUMS}.")
    if problems:
        raise ReleaseError("\n".join(problems))
    return checksums


def sign_release(
    folder: Path,
    version: str,
    key_path: Path,
    requirement: str = "",
    keys: Optional[list[PublicKey]] = None,
    password: Callable[[], str] = lambda: "",
) -> ReleaseClaim:
    version = normalise_version(version)
    checksums = check_release_folder(folder, version)
    claim = ReleaseClaim(version, requirement.strip())
    key_id, seed = read_secret_key(key_path, password)
    signature = signature_text(checksums, key_id, seed, claim.comment())
    try:
        verify(checksums, signature, trusted_keys() if keys is None else keys)
    except SignatureError as error:
        raise ReleaseError(f"{error} The app wouldn't accept this signature.") from error
    (folder / SIGNATURE).write_bytes(signature)
    return claim


def verify_release(folder: Path, version: str, keys: Optional[list[PublicKey]] = None) -> ReleaseClaim:
    version = normalise_version(version)
    checksums = check_release_folder(folder, version)
    try:
        comment = verify(checksums, (folder / SIGNATURE).read_bytes(), trusted_keys() if keys is None else keys)
    except OSError as error:
        raise ReleaseError(f"{folder / SIGNATURE} can't be read.") from error
    except SignatureError as error:
        raise ReleaseError(str(error)) from error
    claim = parse_claim(comment)
    if claim is None or claim.version != version:
        raise ReleaseError(f"The signature is for {comment!r}, not version {version}.")
    return claim


def keygen(name: str, key_dir: Path = KEY_DIR, public_dir: Path = PUBLIC_KEY_DIR) -> tuple[Path, Path]:
    if not re.fullmatch(r"[a-z0-9-]+", name):
        raise ReleaseError("A key name is lower-case letters, digits and dashes.")
    secret_path, public_path = key_dir / f"{name}.key", public_dir / f"{name}.pub"
    for path in (secret_path, public_path):
        if path.exists():
            raise ReleaseError(f"{path} already exists. Keys are never replaced.")
    key_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(key_dir, 0o700)
    seed, key_id = os.urandom(32), os.urandom(8)
    # O_EXCL with owner-only permissions from the start: the key is never
    # readable by anyone else, even for a moment.
    descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(secret_key_text(key_id, seed, name))
    public_dir.mkdir(parents=True, exist_ok=True)
    public_path.write_text(public_key_text(key_id, public_key_of(seed), name), encoding="ascii")
    return secret_path, public_path


# -- command line -----------------------------------------------------------------

def _ask_password() -> str:
    return getpass.getpass("Password for the secret key: ")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sign DoubleClick Fixer releases.")
    commands = parser.add_subparsers(dest="command", required=True)
    sign = commands.add_parser("sign", help="sign a downloaded release folder")
    sign.add_argument("version")
    sign.add_argument("folder", type=Path)
    sign.add_argument("--key", type=Path, default=KEY_DIR / f"{PRIMARY}.key")
    sign.add_argument("--requirement", default="")
    check = commands.add_parser("verify", help="check a signed release folder")
    check.add_argument("version")
    check.add_argument("folder", type=Path)
    make = commands.add_parser("keygen", help="make a new key pair")
    make.add_argument("name")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "sign":
            claim = sign_release(
                arguments.folder, arguments.version, arguments.key, arguments.requirement, password=_ask_password
            )
            print(f"Signed {CHECKSUMS} as {claim.comment()!r}: {arguments.folder / SIGNATURE}")
        elif arguments.command == "verify":
            claim = verify_release(arguments.folder, arguments.version)
            print(f"Good signature: {claim.comment()!r}")
        else:
            secret_path, public_path = keygen(arguments.name)
            key = parse_public_key(public_path.read_text(encoding="ascii"))
            print(
                f"Key {key.key_id_text}\n  secret: {secret_path}\n  public: {public_path}\n"
                "Not trusted yet: add its key line to RELEASE_KEYS in app/updater.py. Only app\n"
                "versions built with it accept its signatures."
            )
    except (ReleaseError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
