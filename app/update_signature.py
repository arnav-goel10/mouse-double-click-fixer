"""Release signatures: minisign files, checked in pure Python.

Every release carries SHA256SUMS.txt and SHA256SUMS.txt.minisig, a minisign
signature the owner makes offline (tools/sign_release.py). The checksums vouch
for each file in the release, and the signature vouches for the checksums, so
an update proves it came from the owner's key and not merely from someone who
can upload files to the GitHub release.

The files, as minisign writes them (https://jedisct1.github.io/minisign/):

    public key   base64("Ed" | key id: 8 bytes | Ed25519 public key: 32 bytes)

    signature    untrusted comment: <free text, not signed>
                 base64(algorithm: 2 bytes | key id: 8 bytes | signature: 64 bytes)
                 trusted comment: <one line, signed>
                 base64(global signature: 64 bytes)

The algorithm "ED" signs the BLAKE2b-512 hash of the file (what minisign
writes by default); "Ed" is its legacy format and signs the file itself. The global
signature covers the 64-byte signature followed by the trusted comment, so a
comment can't be moved onto another file's signature. The untrusted comment
lines carry nothing that is checked, so they are skipped.

The trusted comment says which release the signature is for: "dcf <version>",
optionally followed by " dr=<designated requirement>" when a release moves the
macOS app to a new signing identity (see `ReleaseClaim`).

Ed25519 follows RFC 8032 and its reference code. Pure Python is much slower
than libsodium, a few milliseconds per signature, but an update checks one
small file, and it needs nothing outside the standard library. tools/sign_release.py signs with the same
curve arithmetic, which is why the point helpers here are public.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass
from typing import Iterable, Optional

PREHASHED = b"ED"
LEGACY = b"Ed"
UNTRUSTED_PREFIX = "untrusted comment: "
TRUSTED_PREFIX = "trusted comment: "


class SignatureError(ValueError):
    """A key or signature that can't be read, or a signature that doesn't verify."""


# -- Ed25519 (RFC 8032) -----------------------------------------------------------

#: The field prime and the order of the base point.
P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, P - 2, P) % P
_SQRT_M1 = pow(2, (P - 1) // 4, P)


def _recover_x(y: int, sign: int) -> Optional[int]:
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, P - 2, P) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * _SQRT_M1 % P
    if (x * x - x2) % P:
        return None
    return P - x if (x & 1) != sign else x


# Points are kept in extended coordinates (X, Y, Z, T), with x = X/Z, y = Y/Z
# and x*y = T/Z, so adding two points needs no division.
_BASE_Y = 4 * pow(5, P - 2, P) % P
_BASE_X = _recover_x(_BASE_Y, 0)
BASE = (_BASE_X, _BASE_Y, 1, _BASE_X * _BASE_Y % P)
_NEUTRAL = (0, 1, 1, 0)


def add_points(p: tuple, q: tuple) -> tuple:
    a = (p[1] - p[0]) * (q[1] - q[0]) % P
    b = (p[1] + p[0]) * (q[1] + q[0]) % P
    c = 2 * p[3] * q[3] * _D % P
    d = 2 * p[2] * q[2] % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def multiply_point(scalar: int, point: tuple) -> tuple:
    result = _NEUTRAL
    while scalar > 0:
        if scalar & 1:
            result = add_points(result, point)
        point = add_points(point, point)
        scalar >>= 1
    return result


def _same_point(p: tuple, q: tuple) -> bool:
    return (p[0] * q[2] - q[0] * p[2]) % P == 0 and (p[1] * q[2] - q[1] * p[2]) % P == 0


def encode_point(point: tuple) -> bytes:
    z_inverse = pow(point[2], P - 2, P)
    x = point[0] * z_inverse % P
    y = point[1] * z_inverse % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def decode_point(data: bytes) -> Optional[tuple]:
    """The point a 32-byte encoding names; None if it names none."""
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


def challenge(r: bytes, public_key: bytes, message: bytes) -> int:
    return int.from_bytes(hashlib.sha512(r + public_key + message).digest(), "little") % L


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """RFC 8032 section 5.1.7: is `signature` the key's signature of `message`?"""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = decode_point(public_key)
    r = decode_point(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= L:  # a second encoding of the same signature
        return False
    k = challenge(signature[:32], public_key, message)
    return _same_point(multiply_point(s, BASE), add_points(r, multiply_point(k, a)))


# -- minisign files ---------------------------------------------------------------

@dataclass(frozen=True)
class PublicKey:
    key_id: bytes
    key: bytes

    @property
    def key_id_text(self) -> str:
        return key_id_text(self.key_id)


@dataclass(frozen=True)
class Signature:
    algorithm: bytes
    key_id: bytes
    signature: bytes
    trusted_comment: str
    global_signature: bytes


def key_id_text(key_id: bytes) -> str:
    """The key id as minisign prints it: a little-endian number in hex."""
    return key_id[::-1].hex().upper()


def _decode(line: str, length: int, what: str) -> bytes:
    try:
        data = base64.b64decode(line.strip(), validate=True)
    except (binascii.Error, ValueError) as error:
        raise SignatureError(f"The {what} isn't valid base64.") from error
    if len(data) != length:
        raise SignatureError(f"The {what} has the wrong length.")
    return data


def _lines(text: str) -> list[str]:
    """The non-blank lines, without minisign's untrusted comments."""
    return [
        line.rstrip("\r")
        for line in text.split("\n")
        if line.strip() and not line.startswith(UNTRUSTED_PREFIX)
    ]


def parse_public_key(text: str) -> PublicKey:
    """A minisign public key: the contents of a .pub file, or its key line."""
    lines = _lines(text)
    if len(lines) != 1:
        raise SignatureError("A public key is one line of base64.")
    data = _decode(lines[0], 42, "public key")
    if data[:2] != LEGACY:
        raise SignatureError("The public key isn't an Ed25519 key.")
    return PublicKey(key_id=data[2:10], key=data[10:])


def parse_signature(data: bytes) -> Signature:
    """A minisign signature file."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SignatureError("The signature isn't text.") from error
    lines = _lines(text)
    if len(lines) != 3 or not lines[1].startswith(TRUSTED_PREFIX):
        raise SignatureError("The signature file isn't in minisign's format.")
    signature = _decode(lines[0], 74, "signature")
    if signature[:2] not in (PREHASHED, LEGACY):
        raise SignatureError("The signature uses an unknown algorithm.")
    return Signature(
        algorithm=signature[:2],
        key_id=signature[2:10],
        signature=signature[10:],
        trusted_comment=lines[1][len(TRUSTED_PREFIX):],
        global_signature=_decode(lines[2], 64, "trusted comment's signature"),
    )


def signed_payload(algorithm: bytes, message: bytes) -> bytes:
    """What the Ed25519 signature covers: the file, or its BLAKE2b-512 hash."""
    return hashlib.blake2b(message).digest() if algorithm == PREHASHED else message


def verify(message: bytes, signature_file: bytes, keys: Iterable[PublicKey]) -> str:
    """Check a minisign signature of `message` against trusted keys.

    Returns the trusted comment. Raises SignatureError unless one of `keys`
    made both the signature and the trusted comment's signature.
    """
    signature = parse_signature(signature_file)
    key = next((key for key in keys if key.key_id == signature.key_id), None)
    if key is None:
        raise SignatureError(f"Signed with key {key_id_text(signature.key_id)}, which isn't trusted.")
    if not ed25519_verify(key.key, signed_payload(signature.algorithm, message), signature.signature):
        raise SignatureError("The signature doesn't match the file.")
    comment = signature.trusted_comment.encode("utf-8")
    if not ed25519_verify(key.key, signature.signature + comment, signature.global_signature):
        raise SignatureError("The trusted comment isn't signed.")
    return signature.trusted_comment


# -- what a release signature says ------------------------------------------------

COMMENT_PATTERN = re.compile(r"dcf (?P<version>\S+)(?: dr=(?P<requirement>\S.*))?")


@dataclass(frozen=True)
class ReleaseClaim:
    """The trusted comment of a release signature.

    `requirement` is empty unless the release moves the macOS app to a new
    signing identity. The updater normally refuses an app whose designated
    requirement differs from the installed one, because that is what macOS
    keys the Accessibility grant on; a signed comment naming the new
    requirement is the owner saying the change is intended.
    """

    version: str
    requirement: str = ""

    def comment(self) -> str:
        return f"dcf {self.version}" + (f" dr={self.requirement}" if self.requirement else "")


def parse_claim(comment: str) -> Optional[ReleaseClaim]:
    match = COMMENT_PATTERN.fullmatch(comment)
    if match is None:
        return None
    return ReleaseClaim(match.group("version"), match.group("requirement") or "")
