"""Write the GitHub release page for a version, or refresh the older ones.

    python tools/release_notes.py 0.2.11 owner/repo [--latest 0.2.11] > notes.md
    python tools/release_notes.py --point-older owner/repo [--apply]

The page is installer/release_notes.md (downloads and first-launch help)
followed by that version's section of CHANGELOG.md, so the notes are written
once, in the changelog, and every release page shows them. With --latest, an
older release says that a newer one exists. A pre-release (1.1.0-rc.1) shows
its own section if the changelog has one, else its version's, else the
"Unreleased" one, under a line saying installed copies won't update to it.

--point-older rewrites every published release page other than the latest
release's, so each says a newer version exists. Without --apply it only
prints what it would write. The Release pages workflow runs it when a release
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
UNRELEASED = "Unreleased"
#: The first version whose release carries THIRD_PARTY_NOTICES.md.
NOTICES_SINCE = (1, 0, 0)


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
        parts.append(
            f"> **A newer version is available:** [DoubleClick Fixer {latest}]"
            f"(https://github.com/{repo}/releases/latest). Download that one instead.\n"
        )
    if "-" in version:
        parts.append(
            "> **This is a pre-release, for testing.** Installed copies of DoubleClick Fixer don't update to it.\n"
        )
    parts.append(template.replace("__VERSION__", version).replace("__REPO__", repo).strip())
    parts.append(f"## What's new\n\n{unwrap(section)}")
    if version_tuple(version) >= NOTICES_SINCE:
        parts.append(
            "**Third-party software:** "
            f"[THIRD_PARTY_NOTICES.md](https://github.com/{repo}/releases/download/v{version}/THIRD_PARTY_NOTICES.md) "
            "lists the open-source software in DoubleClick Fixer, its licences and where to get its source."
        )
    previous = previous_version(version, changelog)
    if previous:
        parts.append(f"**All changes:** https://github.com/{repo}/compare/v{previous}...v{version}")
    return "\n\n".join(parts) + "\n"


# -- refreshing older release pages ----------------------------------------------------

def _gh(*arguments: str) -> str:
    result = subprocess.run(["gh", *arguments], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"`gh {' '.join(arguments[:2])}` failed: {result.stderr.strip()}")
    return result.stdout


def point_older(
    repo: str,
    apply: bool = False,
    gh: Callable[..., str] = _gh,
    say: Callable[[str], None] = print,
) -> list[str]:
    """Rewrite every published release page but the latest one so that it
    points at the latest. Returns the tags rewritten (or that would be)."""
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
        try:
            body = notes(tag[1:], repo, latest)
        except SystemExit as error:
            say(f"{tag}: left alone ({error})")
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
