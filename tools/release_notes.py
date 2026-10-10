"""Write the GitHub release page for a version, or refresh the older ones.

    python tools/release_notes.py 0.2.11 owner/repo [--latest 0.2.11] > notes.md
    python tools/release_notes.py --point-older owner/repo [--apply]

The page is installer/release_notes.md (downloads and first-launch help)
followed by that version's section of CHANGELOG.md, so the notes are written
once, in the changelog, and every release page shows them. With --latest, an
older release says that a newer one exists. A pre-release (1.1.0-rc.1) shows
its own section if the changelog has one, else its version's, else the
"Unreleased" one, under a line saying installed copies won't update to it.

--point-older puts a line at the top of every published release page older
than the latest release saying that a newer version exists (replacing the
line an earlier run put there) and leaves the rest of each page as it was
published; a page for a later version (a pre-release of the next one, say)
is left alone. Without --apply it only prints what it would change. The Release pages workflow runs it when a release
is published; it needs `gh`, signed in with permission to edit releases.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
#: The app's name as people see it (DISPLAY_NAME in app/__init__.py).
NAME = re.search(
    r'^DISPLAY_NAME = "([^"]+)"', (ROOT / "app" / "__init__.py").read_text(encoding="utf-8"), re.MULTILINE
).group(1)
UNRELEASED = "Unreleased"
#: The first version whose release carries the notices files.
NOTICES_SINCE = (1, 0, 0)
#: The first version whose releases carry SHA256SUMS.txt.minisig (pre-releases never do).
SIGNED_SINCE = (1, 0, 0)
#: The line --point-older puts at the top of an older page.
NEWER_BANNER = re.compile(r"\A> \*\*A newer version is available:\*\*[^\n]*\n+")
#: The third-party notices a release carries, one per platform's downloads,
#: with the label its page gives each (tools/sign_release.py's MAC_NOTICES
#: and WINDOWS_NOTICES).
NOTICES = (("macOS", "THIRD_PARTY_NOTICES-macos.md"), ("Windows", "THIRD_PARTY_NOTICES-windows.md"))


def changelog_section(version: str, changelog: str) -> str:
    """The body of '## <version> — <date>' up to the next heading."""
    match = re.search(
        rf"^## {re.escape(version)}(?:\s+—\s+(?P<date>[^\n]+))?\n(?P<body>.*?)(?=^## |\Z)",
        changelog,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise SystemExit(f"CHANGELOG.md has no entry for {version}")
    return match.group("body").strip()


def release_section(version: str, changelog: str) -> str:
    """The changelog section a release page shows. A pre-release falls back
    to its version's section, then to the Unreleased one."""
    candidates = [version]
    if "-" in version:
        candidates += [base_version(version), UNRELEASED]
    for candidate in candidates:
        try:
            return changelog_section(candidate, changelog)
        except SystemExit:
            continue
    raise SystemExit(f"CHANGELOG.md has no entry for {version}")


def base_version(version: str) -> str:
    """'1.1.0' for '1.1.0-rc.1'."""
    return version.split("-", 1)[0]


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", base_version(version)))


def unwrap(markdown: str) -> str:
    """Join hard-wrapped lines: GitHub shows every line break on a release
    page, so the changelog's 78-column wrapping would come out ragged."""
    lines: list[str] = []
    for line in markdown.splitlines():
        continuation = line.startswith("  ") and not line.strip().startswith(("- ", "* "))
        plain = line and not line.startswith(("#", "- ", "* ", "|", ">")) and lines and lines[-1]
        if lines and line.strip() and (continuation or plain) and not lines[-1].startswith("#"):
            lines[-1] = lines[-1].rstrip() + " " + line.strip()
        else:
            lines.append(line)
    return "\n".join(lines)


def previous_version(version: str, changelog: str) -> Optional[str]:
    """The release before this one: the next numbered heading below its own,
    or, for a version with no heading yet, the newest older one."""
    versions = re.findall(r"^## (\d+(?:\.\d+)+)", changelog, re.MULTILINE)
    if version in versions:
        index = versions.index(version)
        return versions[index + 1] if index + 1 < len(versions) else None
    if "-" in version and base_version(version) in versions:
        return previous_version(base_version(version), changelog)
    older = [item for item in versions if version_tuple(item) < version_tuple(version)]
    return older[0] if older else None


def notes(version: str, repo: str, latest: Optional[str] = None) -> str:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    template = (ROOT / "installer" / "release_notes.md").read_text(encoding="utf-8")
    section = release_section(version, changelog)
    parts = []
    if latest and latest != version:
        parts.append(newer_banner(latest, repo))
    pre_release = "-" in version
    if pre_release:
        parts.append(
            f"> **This is a pre-release, for testing.** Installed copies of {NAME} don't update to it."
        )
    if pre_release:
        updates = ""
    else:
        updates = f"Already using {NAME}? It updates itself; there is nothing to download."
    if not pre_release and version_tuple(version) >= SIGNED_SINCE:
        checksums = "The files are listed in `SHA256SUMS.txt`, signed in `SHA256SUMS.txt.minisig`; "
    else:
        checksums = "The files are listed in `SHA256SUMS.txt`; "
    checksums += f"[SECURITY.md](https://github.com/{repo}/blob/main/SECURITY.md#how-updates-are-verified) says how to check them."
    page = template.replace("__VERSION__", version).replace("__REPO__", repo)
    page = page.replace("__UPDATES__", updates).replace("__CHECKSUMS__", checksums)
    parts.append(re.sub(r"\n{3,}", "\n\n", page).strip())
    parts.append(f"## What's new\n\n{unwrap(section)}")
    if version_tuple(version) >= NOTICES_SINCE:
        links = " and ".join(
            f"[{name}](https://github.com/{repo}/releases/download/v{version}/{name}) ({label})" for label, name in NOTICES
        )
        parts.append(
            f"**Third-party software:** {links} list the open-source software in each download, "
            "its licences and where to get its source."
        )
    previous = previous_version(version, changelog)
    if previous:
        parts.append(f"**All changes:** https://github.com/{repo}/compare/v{previous}...v{version}")
    return "\n\n".join(parts) + "\n"


def newer_banner(latest: str, repo: str) -> str:
    return (
        f"> **A newer version is available:** [{NAME} {latest}]"
        f"(https://github.com/{repo}/releases/latest). Download that one instead."
    )


def point_at(body: str, latest: str, repo: str) -> str:
    """An older page as published, with the newer-version line at the top
    (replacing one an earlier run put there)."""
    return newer_banner(latest, repo) + "\n\n" + NEWER_BANNER.sub("", body.lstrip("\ufeff"), count=1).lstrip("\n")


# -- refreshing older release pages ----------------------------------------------------

def _gh(*arguments: str) -> str:
    result = subprocess.run(["gh", *arguments], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"`gh {' '.join(arguments[:2])}` failed: {result.stderr.strip()}")
    return result.stdout


def is_older(version: str, latest: str) -> bool:
    """Whether `version` came before `latest`. A pre-release comes before its
    own version's release (1.0.0-rc.1 before 1.0.0)."""
    return version_tuple(version) < version_tuple(latest) or (
        version_tuple(version) == version_tuple(latest) and "-" in version and "-" not in latest
    )


def point_older(
    repo: str,
    apply: bool = False,
    gh: Callable[..., str] = _gh,
    say: Callable[[str], None] = print,
) -> list[str]:
    """Point every published release page older than the latest release at
    the latest, leaving the rest of each page as it was published. A page
    for a later version (a pre-release of the next one) is left alone.
    Returns the tags changed (or that would be)."""
    latest_tag = gh("api", f"repos/{repo}/releases/latest", "--jq", ".tag_name").strip()
    if not re.fullmatch(r"v\d+(?:\.\d+)+", latest_tag):
        raise SystemExit(f"The latest release's tag isn't a version: {latest_tag!r}")
    latest = latest_tag[1:]
    releases = json.loads(gh("release", "list", "--repo", repo, "--limit", "200", "--json", "tagName,isDraft"))
    changed = []
    for release in releases:
        tag = str(release.get("tagName", ""))
        if release.get("isDraft") or tag == latest_tag:
            continue
        if not re.fullmatch(r"v\d+(?:\.\d+)+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?", tag):
            say(f"{tag}: left alone (not a version tag)")
            continue
        if not is_older(tag[1:], latest):
            say(f"{tag}: left alone (later than the latest release, {latest})")
            continue
        published = gh("release", "view", tag, "--repo", repo, "--json", "body", "--jq", ".body")
        body = point_at(published.rstrip("\n") + "\n", latest, repo)
        if body.rstrip() == published.rstrip():
            say(f"{tag}: already points at {latest}")
            continue
        changed.append(tag)
        if not apply:
            say(f"{tag}: would point at {latest} ({len(body)} characters)")
            continue
        with tempfile.TemporaryDirectory() as folder:
            page = Path(folder) / "notes.md"
            page.write_text(body, encoding="utf-8")
            gh("release", "edit", tag, "--repo", repo, "--notes-file", str(page))
        say(f"{tag}: now points at {latest}")
    return changed


def main(arguments: list[str]) -> int:
    arguments = list(arguments)
    if arguments[:1] == ["--point-older"]:
        if arguments[2:] not in ([], ["--apply"]) or len(arguments) < 2:
            raise SystemExit(__doc__)
        point_older(arguments[1], apply=arguments[2:] == ["--apply"])
        return 0
    latest = None
    if "--latest" in arguments:
        index = arguments.index("--latest")
        latest = arguments[index + 1]
        del arguments[index : index + 2]
    if len(arguments) != 2:
        raise SystemExit(__doc__)
    sys.stdout.write(notes(arguments[0], arguments[1], latest))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
