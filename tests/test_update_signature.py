"""Release signatures: Ed25519 (RFC 8032) and minisign's file format."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import hashlib
import types
import unittest
from unittest import mock

from app import update_signature as signatures
from app.update_signature import (
    L,
    ReleaseClaim,
    SignatureError,
    ed25519_verify,
    parse_claim,
    parse_public_key,
    parse_signature,
    verify,
)
from tools.sign_release import ed25519_sign, public_key_of

# RFC 8032 section 7.1: secret key (seed), public key, message, signature.
RFC_8032 = [
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
        "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
    (
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
        "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    ),
    (  # "TEST SHA(abc)": the message is SHA-512("abc")
        "833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42",
        "ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf",
        "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
        "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f",
        "dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b589"
        "09351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704",
    ),
]

# Made by minisign 0.12 itself (`minisign -G -W`, then `minisign -S -t 'dcf
# 1.2.3'` with and without -l) over MINISIGN_MESSAGE.
MINISIGN_MESSAGE = b"hello world\n"
MINISIGN_PUBLIC_KEY = """untrusted comment: minisign public key D497B6120328AFEF
RWTvrygDEraX1MOspOpau20Qyo9VY/XdzZBnGk1uaMHFGPKnIqquoy4M
"""
MINISIGN_PREHASHED = b"""untrusted comment: signature from minisign secret key
RUTvrygDEraX1M5UZpns1s64JagpsYLo+M0risX86v8DIMX2OAPsG8EN9XxHIXMmWpaV4lAVKGnR25t7TI3FoFnLOf52fALdwAU=
trusted comment: dcf 1.2.3
WY/LS+z/d92SSVnFW3eopDPrNDWWn2eNAW4c6t4v2+TKrKeBTNRf3/zAzC/tWVkrQtNrt5QtlG5xdXN4wv5XAA==
"""
MINISIGN_LEGACY = b"""untrusted comment: signature from minisign secret key
RWTvrygDEraX1DPSCrgSonMMYN/00+EpptTnlk6GCWzvRdLpgb5NayAaML9SDBGvzaIwEycLGOpJ80HAgBys83dA9EDQSeQxTwY=
trusted comment: dcf 1.2.3
9ThYuNFDN+DsVzu7aSLM8LIkBlePKjvobitZTNffM0lPURrqv/6eDonjsieFO0A8SdEi6i+jMzfbIA0G6MMiAA==
"""


class Ed25519Tests(unittest.TestCase):
    def test_rfc_8032_vectors_verify(self) -> None:
        for _seed, public, message, signature in RFC_8032:
            self.assertTrue(
                ed25519_verify(bytes.fromhex(public), bytes.fromhex(message), bytes.fromhex(signature)), message
            )

    def test_the_signer_reproduces_the_rfc_signatures(self) -> None:
        # Ed25519 signatures are deterministic, so this checks the signer
        # against the RFC byte for byte.
        for seed, public, message, signature in RFC_8032:
            self.assertEqual(public_key_of(bytes.fromhex(seed)).hex(), public)
            self.assertEqual(ed25519_sign(bytes.fromhex(seed), bytes.fromhex(message)).hex(), signature)

    def test_any_change_is_refused(self) -> None:
        _seed, public, message, signature = RFC_8032[2]
        public, message, signature = bytes.fromhex(public), bytes.fromhex(message), bytes.fromhex(signature)
        self.assertFalse(ed25519_verify(public, message + b"\0", signature))
        self.assertFalse(ed25519_verify(public, b"", signature))
        for index in (0, 31, 32, 63):
            flipped = bytearray(signature)
            flipped[index] ^= 0x01
            self.assertFalse(ed25519_verify(public, message, bytes(flipped)), index)
        other = bytes.fromhex(RFC_8032[0][1])
        self.assertFalse(ed25519_verify(other, message, signature))
        self.assertFalse(ed25519_verify(public, message, signature[:63]))

    def test_a_second_encoding_of_s_is_refused(self) -> None:
        _seed, public, message, signature = (bytes.fromhex(part) for part in RFC_8032[1])
        s = int.from_bytes(signature[32:], "little")
        malleable = signature[:32] + (s + L).to_bytes(32, "little")
        self.assertFalse(ed25519_verify(public, message, malleable))

    def test_points_that_dont_decode_are_refused(self) -> None:
        _seed, public, message, signature = (bytes.fromhex(part) for part in RFC_8032[1])
        not_a_point = (2**255 - 19 + 1).to_bytes(32, "little")  # y >= p
        self.assertFalse(ed25519_verify(not_a_point, message, signature))
        self.assertFalse(ed25519_verify(public, message, not_a_point + signature[32:]))


class MinisignFormatTests(unittest.TestCase):
    key = parse_public_key(MINISIGN_PUBLIC_KEY)

    def test_minisign_signatures_verify_in_both_formats(self) -> None:
        self.assertEqual(self.key.key_id_text, "D497B6120328AFEF")
        for signature, algorithm in ((MINISIGN_PREHASHED, b"ED"), (MINISIGN_LEGACY, b"Ed")):
            self.assertEqual(parse_signature(signature).algorithm, algorithm)
            self.assertEqual(verify(MINISIGN_MESSAGE, signature, [self.key]), "dcf 1.2.3")

    def test_a_changed_file_is_refused(self) -> None:
        for signature in (MINISIGN_PREHASHED, MINISIGN_LEGACY):
            with self.assertRaisesRegex(SignatureError, "doesn't match"):
                verify(b"hello world!\n", signature, [self.key])

    def test_a_changed_trusted_comment_is_refused(self) -> None:
        swapped = MINISIGN_PREHASHED.replace(b"dcf 1.2.3", b"dcf 9.9.9")
        with self.assertRaisesRegex(SignatureError, "trusted comment"):
            verify(MINISIGN_MESSAGE, swapped, [self.key])

    def test_the_untrusted_comment_is_not_checked(self) -> None:
        relabelled = MINISIGN_PREHASHED.replace(b"signature from minisign secret key", b"anything at all")
        self.assertEqual(verify(MINISIGN_MESSAGE, relabelled, [self.key]), "dcf 1.2.3")
        self.assertEqual(verify(MINISIGN_MESSAGE, relabelled.replace(b"\n", b"\r\n"), [self.key]), "dcf 1.2.3")

    def test_an_untrusted_key_is_refused(self) -> None:
        other = parse_public_key("RWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3")
        with self.assertRaisesRegex(SignatureError, "isn't trusted"):
            verify(MINISIGN_MESSAGE, MINISIGN_PREHASHED, [other])
        with self.assertRaisesRegex(SignatureError, "isn't trusted"):
            verify(MINISIGN_MESSAGE, MINISIGN_PREHASHED, [])

    def test_malformed_files_are_refused(self) -> None:
        lines = MINISIGN_PREHASHED.decode().splitlines()
        for broken in (
            b"",
            b"\xff\xfe",
            "\n".join(lines[:3]).encode(),  # no comment signature
            "\n".join([lines[0], lines[1], "dcf 1.2.3", lines[3]]).encode(),  # no "trusted comment:"
            "\n".join([lines[0], lines[1][:-8] + "!!!!!!!=", lines[2], lines[3]]).encode(),
            "\n".join([lines[0], lines[1], lines[2], lines[3][:20]]).encode(),
        ):
            with self.assertRaises(SignatureError, msg=broken):
                verify(MINISIGN_MESSAGE, broken, [self.key])

    def test_public_keys(self) -> None:
        line = MINISIGN_PUBLIC_KEY.splitlines()[1]
        self.assertEqual(parse_public_key(line), self.key)
        self.assertEqual(parse_public_key(f"  {line}\r\n"), self.key)
        for broken in ("", "not base64!", line[:-4], f"{line}\n{line}"):
            with self.assertRaises(SignatureError, msg=broken):
                parse_public_key(broken)
        with self.assertRaisesRegex(SignatureError, "Ed25519"):
            parse_public_key(signatures.base64.b64encode(b"XX" + bytes(40)).decode())


class ReleaseClaimTests(unittest.TestCase):
    def test_comment_format(self) -> None:
        self.assertEqual(parse_claim("dcf 1.0.1"), ReleaseClaim("1.0.1"))
        requirement = 'identifier "com.doubleclickfixer.app" and certificate leaf = H"ab"'
        claim = parse_claim(f"dcf 1.1.0 dr={requirement}")
        self.assertEqual(claim, ReleaseClaim("1.1.0", requirement))
        self.assertEqual(claim.comment(), f"dcf 1.1.0 dr={requirement}")
        self.assertEqual(ReleaseClaim("1.0.1").comment(), "dcf 1.0.1")
        for wrong in ("", "dcf", "dcf ", "dcf 1.0.1 ", "dcf 1.0.1 dr=", "dcf 1.0.1 extra", "DCF 1.0.1"):
            self.assertIsNone(parse_claim(wrong), wrong)


class SelfTestTests(unittest.TestCase):
    """update_signature.self_test, which a packaged build runs before release."""

    def test_passes_with_the_standard_library(self) -> None:
        signatures.self_test()

    def test_a_build_without_blake2b_fails_it(self) -> None:
        # What a frozen build that lost hashlib's _blake2 module looks like.
        without_blake2 = types.SimpleNamespace(sha512=hashlib.sha512)
        with mock.patch.object(signatures, "hashlib", without_blake2), \
                self.assertRaisesRegex(SignatureError, "can't be checked in this build.*blake2b"):
            signatures.self_test()

    def test_a_verifier_that_accepts_anything_fails_it(self) -> None:
        with mock.patch.object(signatures, "ed25519_verify", return_value=True), \
                self.assertRaisesRegex(SignatureError, "RFC 8032"):
            signatures.self_test()
        # A prehash that ignores the file would let any file through.
        ignores_the_file = hashlib.blake2b(b"hello world\n").digest()
        with mock.patch.object(signatures, "signed_payload", return_value=ignores_the_file), \
                self.assertRaisesRegex(SignatureError, "changed file passed"):
            signatures.self_test()


if __name__ == "__main__":
    unittest.main()
