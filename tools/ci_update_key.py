"""A throwaway update key for CI's end-to-end test of the Windows updater.

A packaged app installs only updates signed with the release keys, and those
never leave the owner's Mac. So CI's windows-install job makes a key pair for
that one run, and builds the app with the public half inside it
(doubleclick-fixer.spec bundles the file DCF_CI_UPDATE_KEY names, and
app/updater.py trusts it only when it is there). The end-to-end script then
signs its stand-in release with the secret half. release.yml refuses to build
with DCF_CI_UPDATE_KEY set, and runs `check` on what it built, so no release
ever trusts this key.

    python tools/ci_update_key.py make DIR
        Writes DIR/dcf-ci-update-key.key and DIR/dcf-ci-update-key.pub and
        prints the public key's path.
    python tools/ci_update_key.py sign VERSION FILE KEY
        Signs FILE (a SHA256SUMS.txt) for VERSION, as tools/sign_release.py
        does, writing FILE.minisig.
    python tools/ci_update_key.py check PATH...
        Fails if any of these files, or any file in these folders, carries
        the key: the file itself, or its name in a one-file exe's table of
        contents.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.update_signature import ReleaseClaim, parse_public_key, verify  # noqa: E402
from tools.sign_release import keygen, read_secret_key, signature_text  # noqa: E402

#: The name the app looks for beside its own files (app/updater.py's CI_KEY_FILE).
KEY_NAME = "dcf-ci-update-key"
KEY_FILE = KEY_NAME + ".pub"
MARKER = KEY_NAME.encode("ascii")


def make(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    _secret, public = keygen(KEY_NAME, key_dir=folder, public_dir=folder)
    return public


def sign(version: str, checksums: Path, key: Path) -> Path:
    message = checksums.read_bytes()
    key_id, seed = read_secret_key(key)
    signature = signature_text(message, key_id, seed, ReleaseClaim(version).comment())
    public = parse_public_key(key.with_suffix(".pub").read_text(encoding="ascii"))
    verify(message, signature, [public])  # what the app will do
    target = checksums.with_name(checksums.name + ".minisig")
    target.write_bytes(signature)
    return target


def carriers(paths: list[Path]) -> list[Path]:
    found = []
    for path in paths:
        files = sorted(item for item in path.rglob("*") if item.is_file()) if path.is_dir() else [path]
        for item in files:
            if item.name == KEY_FILE:
                found.append(item)
            elif item.suffix.lower() == ".exe" and MARKER in item.read_bytes():
                found.append(item)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    make_command = commands.add_parser("make")
    make_command.add_argument("folder", type=Path)
    sign_command = commands.add_parser("sign")
    sign_command.add_argument("version")
    sign_command.add_argument("file", type=Path)
    sign_command.add_argument("key", type=Path)
    check_command = commands.add_parser("check")
    check_command.add_argument("paths", type=Path, nargs="+")
    arguments = parser.parse_args(argv)
    if arguments.command == "make":
        print(make(arguments.folder))
    elif arguments.command == "sign":
        print(sign(arguments.version.removeprefix("v"), arguments.file, arguments.key))
    else:
        missing = [path for path in arguments.paths if not path.exists()]
        if missing:
            print(f"error: {', '.join(map(str, missing))} not found", file=sys.stderr)
            return 2
        found = carriers(arguments.paths)
        for path in found:
            print(f"error: {path} carries CI's throwaway update key", file=sys.stderr)
        return 1 if found else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
