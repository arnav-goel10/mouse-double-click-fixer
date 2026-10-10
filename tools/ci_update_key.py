"""A throwaway update key for CI's end-to-end test of the Windows updater.

A packaged app installs only updates signed with the release keys, and those
never leave the owner's Mac. So CI's windows-install job makes a key pair for
that one run and builds the app to trust the public half. The end-to-end
script then signs its stand-in release with the secret half.

Trusting the key is decided when the app is built, never when it runs. With
DCF_CI_UPDATE_KEY naming the public key, doubleclick-fixer.spec freezes a
start-up hook named dcf-ci-update-key into a Windows build (spec_runtime_hooks
below writes it); the hook sets app.build_flags.CI_UPDATE_KEY before the app's
own code runs. Nothing outside the executable can add or change it. Without
the variable, and on macOS whatever it says, there is no hook. Which ref is
being built plays no part: release.yml refuses to build with the variable set
and runs `check` on what it built, so no release carries the hook.

    python tools/ci_update_key.py make DIR
        Writes DIR/dcf-ci-update-key.key and DIR/dcf-ci-update-key.pub and
        prints the public key's path.
    python tools/ci_update_key.py sign VERSION FILE KEY
        Signs FILE (a SHA256SUMS.txt) for VERSION, as tools/sign_release.py
        does, writing FILE.minisig.
    python tools/ci_update_key.py check PATH...
        Fails if any of these files, or any file in these folders, carries
        the key or the hook: a file named dcf-ci-update-key.*, or that name in
        an executable's bundled archive, where PyInstaller lists the start-up
        hooks it froze in (in the one-file exe and the installed folder's exe
        alike).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.update_signature import ReleaseClaim, SignatureError, parse_public_key, verify  # noqa: E402
from tools.sign_release import keygen, read_secret_key, signature_text  # noqa: E402

KEY_NAME = "dcf-ci-update-key"
KEY_FILE = KEY_NAME + ".pub"
#: The variable that asks doubleclick-fixer.spec to build the key in.
ENV = "DCF_CI_UPDATE_KEY"
#: The start-up hook's file name. PyInstaller lists a hook in the executable's
#: archive under its file name without .py, so a build with the hook carries
#: MARKER as plain bytes.
HOOK_FILE = KEY_NAME + ".py"
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


def hook_source(key_line: str) -> str:
    """The start-up hook that makes a build trust `key_line`."""
    return (
        "# Written by doubleclick-fixer.spec, for CI's end-to-end Windows build only\n"
        f"# ({ENV} was set; see tools/ci_update_key.py). Frozen into the executable,\n"
        "# it runs before the app's own code; nothing outside the build can change it.\n"
        "import app.build_flags\n"
        "\n"
        f"app.build_flags.CI_UPDATE_KEY = {key_line!r}\n"
    )


def spec_runtime_hooks(environ: Mapping[str, str], platform: str, hook_dir: Path) -> list[str]:
    """The start-up hooks doubleclick-fixer.spec adds for CI's key.

    One hook, written into `hook_dir`, when `environ` names a public key in
    DCF_CI_UPDATE_KEY and the build is for Windows; otherwise none. The ref
    being built (GITHUB_REF) plays no part, so CI's job builds the same when
    release.yml runs it for a tag. A macOS build ignores the variable.
    """
    hook = hook_dir / HOOK_FILE
    hook.unlink(missing_ok=True)  # a hook only ever comes from this build
    named = environ.get(ENV, "")
    if not named or not platform.startswith("win"):
        return []
    try:
        text = Path(named).read_text(encoding="ascii")
        key = parse_public_key(text)
    except (OSError, ValueError, SignatureError) as error:
        raise SystemExit(f"{ENV} must name a minisign public key file ({named}): {error}") from error
    key_line = [line.strip() for line in text.splitlines() if line.strip()][-1]
    hook_dir.mkdir(parents=True, exist_ok=True)
    hook.write_text(hook_source(key_line), encoding="ascii")
    print(f"{ENV}: this build trusts CI's throwaway update key {key.key_id_text}")
    return [str(hook.resolve())]


def carriers(paths: list[Path]) -> list[Path]:
    found = []
    for path in paths:
        files = sorted(item for item in path.rglob("*") if item.is_file()) if path.is_dir() else [path]
        for item in files:
            if item.name.lower().startswith(KEY_NAME):
                found.append(item)
            elif item.suffix.lower() in (".exe", ".pkg") and MARKER in item.read_bytes():
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
        if not found:
            print(f"Neither CI's update key nor its start-up hook is in {', '.join(map(str, arguments.paths))}")
        return 1 if found else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
