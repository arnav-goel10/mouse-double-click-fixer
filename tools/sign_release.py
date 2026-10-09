"""Sign and publish a release with the update key.

An installed copy of DoubleClick Fixer installs an update only when the
release's SHA256SUMS.txt carries a minisign signature from one of the keys
built into the app: RELEASE_KEYS in app/updater.py, which this tool reads too
(tools/keys/ has copies for the minisign tool). The secret keys never go to
GitHub or CI; they stay on the owner's Mac, and releases are signed there.

Pushing a tag makes release.yml build, test and put up a draft release.
Every step of a release, in order (`python3 tools/sign_release.py --help`
prints them too):

    # 1. Merge what is being released into main:
    git switch main && git pull --ff-only
    git merge --no-ff BRANCH
    # 2. Set the version in app/__init__.py (__version__ = "1.0.1"), turn the
    #    top of CHANGELOG.md into "## 1.0.1 — YYYY-MM-DD" (today's date), and
    #    check both as release.yml will, then commit and push:
    GITHUB_REF=refs/tags/v1.0.1 python3 -m unittest tests.test_release
    git commit -am "DoubleClick Fixer 1.0.1"
    git push origin main
    # 3. Tag that commit and push the tag; release.yml builds it:
    git tag -a v1.0.1 -m "DoubleClick Fixer 1.0.1"
    git push origin v1.0.1
    # 4. Wait until the tag's run exists (it can take a few seconds to
    #    appear), then until it finishes; it puts up the draft:
    until run="$(gh run list --workflow release.yml --branch v1.0.1 --event push --limit 1 --json databaseId --jq '.[].databaseId')" && [ -n "$run" ]; do sleep 5; done
    gh run watch "$run" --exit-status
    # 5. Check the draft, then sign and publish it, on this Mac (the one with
    #    the update keys, System Integrity Protection on), still at the tag:
    python3 tools/sign_release.py publish v1.0.1 --dry-run
    python3 tools/sign_release.py publish v1.0.1

`publish` downloads the draft's files and the artifacts of the release.yml
run that built the tag (the push of that tag, at the commit it points at),
and refuses unless:

- the draft holds exactly the release's files, each byte for byte the file
  the run built (and the digest GitHub lists for it);
- SHA256SUMS.txt lists every file with the right hash, and the macOS zip holds
  exactly one app, of the tag's version;
- that app's signature is valid and its designated requirement is the one
  installed copies have (APP_REQUIREMENT), so macOS keeps their Accessibility
  permission;
- its THIRD_PARTY_NOTICES.md is the draft's THIRD_PARTY_NOTICES-macos.md;
- tools/macos_injection_check.sh passes on that app with all its legs, which
  needs System Integrity Protection on (it is off on GitHub's Macs, so the
  build there skips the leg that needs it): no library or program named in
  the app's environment loads into it or runs as it. This needs clang, from
  Xcode's command line tools.

It then signs SHA256SUMS.txt with the trusted comment "dcf 1.0.1", checks the
signature against RELEASE_KEYS, uploads SHA256SUMS.txt.minisig, checks that
nothing on the draft changed meanwhile, and publishes the draft as the latest
release. Publishing starts the Release pages workflow, which points the older
release pages at the new one. --dry-run does every check and signs, uploads
and publishes nothing. A tag with a suffix (v1.1.0-rc.1) is a pre-release:
it is checked the same way but never signed, so installed copies, which only
look at the latest full release anyway, can never install it.

    --run ID            the run to compare with, when more than one built the tag
    --workdir DIR       download into DIR (empty) instead of a temporary folder
    --key PATH          sign with another secret key (default: the primary key)
    --requirement DR    for a release that moves the macOS app to a new signing
                        identity: the new designated requirement, exactly as
                        `codesign -d -r- "DoubleClick Fixer.app"` prints it after
                        "designated =>". The app must have it, the signed
                        comment then names it, and installed copies accept it.
    --repo OWNER/NAME   another repository (default: the one copies update from)

The steps `publish` takes, by hand (after comparing the files with the run's
artifacts):

    gh release download v1.0.1 --dir ~/dcf-release-1.0.1
    ditto -x -k ~/dcf-release-1.0.1/DoubleClickFixer-macos.zip ~/dcf-app-1.0.1
    bash tools/macos_injection_check.sh ~/dcf-app-1.0.1/"DoubleClick Fixer.app" --require-sip
    python3 tools/sign_release.py sign 1.0.1 ~/dcf-release-1.0.1
    gh release upload v1.0.1 ~/dcf-release-1.0.1/SHA256SUMS.txt.minisig
    gh release edit v1.0.1 --draft=false --latest

`sign` checks every file in the folder against SHA256SUMS.txt and the macOS
zip's app version, as above, signs, checks the signature against RELEASE_KEYS
and writes SHA256SUMS.txt.minisig (it takes --key and --requirement too).
Upload the signature before taking the release out of draft: copies of the app
that check signatures don't offer a release without one, and say so.

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
import json
import os
import plistlib
import re
import subprocess
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


# -- publishing a draft -------------------------------------------------------------

#: The workflow that builds a tag and puts its draft release up.
RELEASE_WORKFLOW = "release.yml"
RELEASE_WORKFLOW_NAME = "Release"
#: The third-party notices each platform's downloads ship: the macOS app's
#: Contents/Resources/THIRD_PARTY_NOTICES.md, and the one the Windows
#: installer puts beside the app.
MAC_NOTICES = "THIRD_PARTY_NOTICES-macos.md"
WINDOWS_NOTICES = "THIRD_PARTY_NOTICES-windows.md"
#: Every file a release carries besides SHA256SUMS.txt, by the workflow
#: artifact it was built into.
ARTIFACTS = {
    "DoubleClickFixer-windows": ("DoubleClickFixer.exe", "DoubleClickFixer-Setup.exe", WINDOWS_NOTICES),
    "DoubleClickFixer-macos": ("DoubleClickFixer.dmg", MAC_ZIP, MAC_NOTICES),
}
RELEASE_FILES = tuple(sorted(name for names in ARTIFACTS.values() for name in names)) + (CHECKSUMS,)
#: The macOS app's designated requirement. macOS keeps the Accessibility
#: permission for an update only while it stays the same.
APP_REQUIREMENT = (
    'identifier "com.doubleclickfixer.app" and certificate root = H"81a512665a945aebb386f04541f5c41f341a6c31"'
)
#: Checks that nothing named in the app's environment loads into it or runs as
#: it. --require-sip makes it run every leg, and fail where it can't.
INJECTION_CHECK = ROOT / "tools" / "macos_injection_check.sh"
TAG_PATTERN = re.compile(r"v(?P<version>\d+\.\d+\.\d+)(?P<suffix>-[0-9A-Za-z][0-9A-Za-z.-]*)?")


def default_repository(source: Optional[Path] = None) -> str:
    """REPOSITORY in app/updater.py: where installed copies look for updates."""
    source = UPDATER_SOURCE if source is None else source
    match = re.search(r'^REPOSITORY = "([^"]+)"', source.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise ReleaseError(f"{source} names no REPOSITORY.")
    return match.group(1)


def parse_tag(text: str) -> tuple[str, str, bool]:
    """(tag, the app's version, pre-release) for 'v1.0.1' or 'v1.1.0-rc.1'."""
    tag = text.strip()
    tag = tag if tag.startswith("v") else "v" + tag
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ReleaseError(f"{text!r} isn't a release tag like v1.0.1 or v1.1.0-rc.1.")
    return tag, match.group("version"), match.group("suffix") is not None


class Commands:
    """Runs the programs publishing needs (gh, ditto, codesign). Tests swap
    in a fake."""

    def run(self, *command: str, both: bool = False) -> str:
        """The command's output (with `both`, stderr after stdout); a
        ReleaseError if it fails."""
        try:
            result = subprocess.run(list(command), capture_output=True, text=True, check=False)
        except OSError as error:
            raise ReleaseError(f"{command[0]} couldn't be run: {error}") from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise ReleaseError(f"`{' '.join(command)}` failed (exit {result.returncode}): {detail}")
        return result.stdout + (result.stderr if both else "")


class Publisher:
    """Checks a draft release against what its workflow run built, signs it
    and publishes it. See `publish` in the module docstring."""

    def __init__(
        self,
        tag: str,
        workdir: Path,
        repository: str,
        key_path: Path,
        requirement: str = "",
        run_id: Optional[int] = None,
        dry_run: bool = False,
        commands: Optional[Commands] = None,
        keys: Optional[list[PublicKey]] = None,
        password: Callable[[], str] = lambda: "",
        say: Callable[[str], None] = print,
    ) -> None:
        self.tag, self.version, self.prerelease = parse_tag(tag)
        self.workdir = workdir
        self.repository = repository
        self.key_path = key_path
        self.requirement = requirement.strip()
        self.run_id = run_id
        self.dry_run = dry_run
        self.commands = commands or Commands()
        self.keys = keys
        self.password = password
        self.say = say

    # -- GitHub ------------------------------------------------------------------
    def gh(self, *arguments: str) -> str:
        return self.commands.run("gh", *arguments)

    def gh_json(self, *arguments: str) -> object:
        output = self.gh(*arguments)
        try:
            return json.loads(output)
        except ValueError as error:
            raise ReleaseError(f"`gh {' '.join(arguments[:2])}` gave something that isn't JSON: {output[:200]!r}") from error

    def draft_assets(self) -> dict[str, tuple[str, int, str]]:
        """The draft's files, as name -> (asset id, size, GitHub's digest)."""
        data = self.gh_json(
            "release", "view", self.tag, "--repo", self.repository, "--json", "tagName,isDraft,isPrerelease,assets"
        )
        if not isinstance(data, dict) or data.get("tagName") != self.tag:
            raise ReleaseError(f"GitHub returned another release when asked for {self.tag}.")
        if not data.get("isDraft"):
            raise ReleaseError(f"{self.tag} is already published. This only publishes drafts.")
        if bool(data.get("isPrerelease")) != self.prerelease:
            raise ReleaseError(
                f"{self.tag} is {'not ' if self.prerelease else ''}marked as a pre-release, "
                f"but a tag {'with' if self.prerelease else 'without'} a suffix should be."
            )
        assets = {}
        for asset in data.get("assets") or []:
            assets[str(asset.get("name"))] = (str(asset.get("id", "")), int(asset.get("size", 0)), str(asset.get("digest") or ""))
        allowed = set(RELEASE_FILES) | {SIGNATURE}
        problems = [f"The draft has no {name}." for name in RELEASE_FILES if name not in assets]
        problems += [f"The draft has a file no release carries: {name}." for name in sorted(set(assets) - allowed)]
        if problems:
            raise ReleaseError("\n".join(problems))
        return assets

    def tag_commit(self) -> str:
        commit = self.gh("api", f"repos/{self.repository}/commits/{self.tag}", "--jq", ".sha").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ReleaseError(f"Couldn't find the commit {self.tag} points at.")
        return commit

    def build_run(self, commit: str) -> int:
        """The successful run of the release workflow that built this tag."""
        fields = "databaseId,headBranch,headSha,event,conclusion,workflowName"
        if self.run_id is not None:
            runs = [self.gh_json("run", "view", str(self.run_id), "--repo", self.repository, "--json", fields)]
        else:
            runs = self.gh_json(
                "run", "list", "--repo", self.repository, "--workflow", RELEASE_WORKFLOW, "--branch", self.tag,
                "--event", "push", "--limit", "50", "--json", fields,
            )
        if not isinstance(runs, list):
            raise ReleaseError("`gh run list` gave something that isn't a list of runs.")
        matching = [
            run for run in runs
            if isinstance(run, dict)
            and run.get("workflowName") == RELEASE_WORKFLOW_NAME
            and run.get("headBranch") == self.tag
            and run.get("event") == "push"
            and run.get("headSha") == commit
        ]
        good = [run for run in matching if run.get("conclusion") == "success"]
        if self.run_id is not None and not good:
            raise ReleaseError(
                f"Run {self.run_id} isn't a successful {RELEASE_WORKFLOW} run for the push of {self.tag} at {commit[:12]}."
            )
        if not good:
            raise ReleaseError(f"No successful {RELEASE_WORKFLOW} run built {self.tag} ({commit[:12]}).")
        if len(good) > 1:
            numbers = ", ".join(str(run.get("databaseId")) for run in good)
            raise ReleaseError(f"More than one run built {self.tag} ({numbers}); name the one to trust with --run.")
        return int(good[0]["databaseId"])

    # -- checks ------------------------------------------------------------------
    def compare_with_artifacts(self, folder: Path, artifacts: Path, assets: dict) -> None:
        """Every file on the draft is the one the workflow run built, byte for byte."""
        problems = []
        downloaded = {path.name for path in folder.iterdir() if path.is_file()}
        if downloaded != set(assets):
            problems.append(f"Downloading the draft gave {sorted(downloaded)}, not {sorted(assets)}.")
        for name, (_id, _size, digest) in sorted(assets.items()):
            if digest and (folder / name).is_file() and digest != "sha256:" + _sha256(folder / name):
                problems.append(f"{name} doesn't match the digest GitHub lists for it.")
        for artifact, names in ARTIFACTS.items():
            root = artifacts / artifact
            built = {str(path.relative_to(root)): path for path in root.rglob("*") if path.is_file()}
            if set(built) != set(names):
                problems.append(f"The {artifact} artifact holds {sorted(built)}, not {sorted(names)}.")
            for name in names:
                if name in built and (folder / name).is_file() and _sha256(built[name]) != _sha256(folder / name):
                    problems.append(f"{name} on the draft isn't the file the run built ({artifact}).")
        if problems:
            raise ReleaseError("\n".join(problems))

    def unpack_mac_app(self, folder: Path) -> Path:
        """The app in the draft's macOS zip, unpacked."""
        unpacked = self.workdir / "unpacked"
        # ditto keeps the symlinks and extended attributes a signature needs.
        self.commands.run("ditto", "-x", "-k", str(folder / MAC_ZIP), str(unpacked))
        apps = sorted(path for path in unpacked.iterdir() if path.suffix == ".app")
        if len(apps) != 1:
            raise ReleaseError(f"{MAC_ZIP} unpacks to {len(apps)} apps, not one.")
        return apps[0]

    def check_mac_app(self, app: Path) -> str:
        """The designated requirement of the app, once it is the one installed
        copies expect (or the --requirement being moved to)."""
        self.commands.run("codesign", "--verify", "--deep", "--strict", str(app))
        output = self.commands.run("codesign", "-d", "-r-", str(app), both=True)
        found = [line.split("=>", 1)[1].strip() for line in output.splitlines() if line.startswith("designated =>")]
        expected = self.requirement or APP_REQUIREMENT
        if found != [expected]:
            hint = "" if self.requirement else (
                " (Moving to a new signing identity on purpose? Name the new requirement with --requirement.)"
            )
            raise ReleaseError(
                f"The app in {MAC_ZIP} has the designated requirement\n    {found[0] if found else '(none)'}\n"
                f"not\n    {expected}\nmacOS would take the Accessibility permission away from every copy it "
                f"updated.{hint}"
            )
        return found[0]

    def check_mac_notices(self, app: Path, folder: Path) -> None:
        """The draft's macOS notices are the ones inside the app."""
        bundled = app / "Contents" / "Resources" / "THIRD_PARTY_NOTICES.md"
        if not bundled.is_file() or bundled.read_bytes() != (folder / MAC_NOTICES).read_bytes():
            raise ReleaseError(f"{MAC_NOTICES} on the draft isn't the THIRD_PARTY_NOTICES.md inside the app in {MAC_ZIP}.")

    def check_injection(self, app: Path) -> None:
        """tools/macos_injection_check.sh, every leg, on the release's own app."""
        try:
            self.commands.run("bash", str(INJECTION_CHECK), str(app), "--require-sip")
        except ReleaseError as error:
            raise ReleaseError(
                f"The app in {MAC_ZIP} failed tools/macos_injection_check.sh, which needs System Integrity "
                f"Protection on and clang (Xcode's command line tools):\n{error}"
            ) from error

    # -- the whole flow --------------------------------------------------------------
    def publish(self) -> None:
        say = self.say
        assets = self.draft_assets()
        commit = self.tag_commit()
        run = self.build_run(commit)
        say(f"{self.tag}: draft with {len(assets)} files; built by run {run} from {commit[:12]}")

        folder, artifacts = self.workdir / "release", self.workdir / "artifacts"
        folder.mkdir(parents=True)
        self.gh("release", "download", self.tag, "--repo", self.repository, "--dir", str(folder))
        for artifact in ARTIFACTS:
            self.gh("run", "download", str(run), "--repo", self.repository, "--name", artifact, "--dir", str(artifacts / artifact))
        self.compare_with_artifacts(folder, artifacts, assets)
        say("Every file on the draft is the one the run built.")
        check_release_folder(folder, self.version)
        say(f"{CHECKSUMS} lists every file, each hash matches, and the macOS app is version {self.version}.")
        app = self.unpack_mac_app(folder)
        requirement = self.check_mac_app(app)
        say(f"The macOS app is signed with: {requirement}")
        self.check_mac_notices(app, folder)
        say(f"{MAC_NOTICES} is the notices file inside the macOS app.")
        say("Running tools/macos_injection_check.sh on the macOS app (every leg, SIP on)...")
        self.check_injection(app)
        say("No library or program named in the app's environment loads into it or runs as it.")

        signed = SIGNATURE in assets
        if self.prerelease:
            if signed:
                raise ReleaseError(f"A pre-release mustn't carry {SIGNATURE}: installed copies must never install it.")
            say("A pre-release isn't signed, so installed copies never install it.")
        elif signed:
            claim = verify_release(folder, self.version, self.keys)
            if claim.requirement != self.requirement:
                raise ReleaseError(f"The draft's {SIGNATURE} says {claim.comment()!r}; delete it and run this again.")
            say(f"The draft already carries a good signature: {claim.comment()!r}")
        elif self.dry_run:
            if not self.key_path.is_file():
                raise ReleaseError(f"There's no secret key at {self.key_path}.")
            claim = ReleaseClaim(self.version, self.requirement)
            say(f"Would sign {CHECKSUMS} as {claim.comment()!r} with {self.key_path} and upload {SIGNATURE}.")
        else:
            claim = sign_release(folder, self.version, self.key_path, self.requirement, self.keys, self.password)
            self.gh("release", "upload", self.tag, str(folder / SIGNATURE), "--repo", self.repository)
            uploaded = self.workdir / "uploaded"
            self.gh("release", "download", self.tag, "--repo", self.repository, "--pattern", SIGNATURE, "--dir", str(uploaded))
            if (uploaded / SIGNATURE).read_bytes() != (folder / SIGNATURE).read_bytes():
                raise ReleaseError(f"The {SIGNATURE} on the draft isn't the one just uploaded.")
            say(f"Signed {CHECKSUMS} as {claim.comment()!r} and uploaded {SIGNATURE}.")

        latest = "--latest=false" if self.prerelease else "--latest"
        if self.dry_run:
            say(f"Would publish {self.tag} ({'pre-release' if self.prerelease else 'latest release'}). Dry run: nothing changed.")
            return
        # Nothing on the draft may have changed since it was checked.
        now = self.draft_assets()
        changed = [name for name in RELEASE_FILES if now.get(name) != assets.get(name)]
        if changed:
            raise ReleaseError(f"{', '.join(changed)} changed on the draft while it was checked. Run this again.")
        self.gh("release", "edit", self.tag, "--repo", self.repository, "--draft=false", latest)
        say(
            f"Published {self.tag}"
            + (" as a pre-release." if self.prerelease else " as the latest release. The Release pages workflow now "
               "points the older release pages at it.")
        )


def publish_release(
    tag: str,
    workdir: Optional[Path] = None,
    repository: Optional[str] = None,
    key_path: Optional[Path] = None,
    requirement: str = "",
    run_id: Optional[int] = None,
    dry_run: bool = False,
    commands: Optional[Commands] = None,
    keys: Optional[list[PublicKey]] = None,
    password: Callable[[], str] = lambda: "",
    say: Callable[[str], None] = print,
) -> Path:
    """Check, sign and publish the draft for `tag`; returns the folder that
    holds what was checked."""
    _tag, version, _prerelease = parse_tag(tag)
    if workdir is None:
        import tempfile

        workdir = Path(tempfile.mkdtemp(prefix=f"dcf-release-{version}-"))
    elif workdir.exists() and any(workdir.iterdir()):
        raise ReleaseError(f"{workdir} isn't empty.")
    publisher = Publisher(
        tag, workdir, repository or default_repository(), key_path or KEY_DIR / f"{PRIMARY}.key",
        requirement, run_id, dry_run, commands, keys, password, say,
    )
    publisher.publish()
    return workdir


# -- command line -----------------------------------------------------------------

def release_steps() -> str:
    """Every step of a release, as the module docstring lists them (nothing
    when Python was asked to drop docstrings)."""
    doc = __doc__ or ""
    start, end = doc.find("    # 1."), doc.find("\n\n`publish` downloads")
    return "every step of a release:\n\n" + doc[start:end] if 0 <= start < end else ""


def _ask_password() -> str:
    return getpass.getpass("Password for the secret key: ")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sign DoubleClick Fixer releases.", epilog=release_steps(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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
    release = commands.add_parser(
        "publish", help="check, sign and publish a draft release", epilog=release_steps(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    release.add_argument("tag")
    release.add_argument("--dry-run", action="store_true", help="check everything; sign, upload and publish nothing")
    release.add_argument("--run", type=int, help="the release.yml run that built the tag, if more than one did")
    release.add_argument("--workdir", type=Path, help="an empty folder to download into (default: a new temporary one)")
    release.add_argument("--key", type=Path, default=KEY_DIR / f"{PRIMARY}.key")
    release.add_argument("--requirement", default="")
    release.add_argument("--repo", help="OWNER/NAME (default: the one installed copies update from)")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "publish":
            folder = publish_release(
                arguments.tag, arguments.workdir, arguments.repo, arguments.key, arguments.requirement,
                arguments.run, arguments.dry_run, password=_ask_password,
            )
            print(f"What was checked is in {folder}")
        elif arguments.command == "sign":
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
