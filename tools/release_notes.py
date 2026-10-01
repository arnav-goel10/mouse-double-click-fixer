"""Write the GitHub release page for a version.

    python tools/release_notes.py 0.2.11 owner/repo [--latest 0.2.11] > notes.md

The page is installer/release_notes.md (downloads and first-launch help)
followed by that version's section of CHANGELOG.md, so the notes are written
once, in the changelog, and every release page shows them. With --latest, an
older release says that a newer one exists.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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


def previous_version(version: str, changelog: str) -> str | None:
    versions = re.findall(r"^## (\d+(?:\.\d+)+)", changelog, re.MULTILINE)
    if version in versions and versions.index(version) + 1 < len(versions):
        return versions[versions.index(version) + 1]
    return None


def notes(version: str, repo: str, latest: str | None = None) -> str:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    template = (ROOT / "installer" / "release_notes.md").read_text(encoding="utf-8")
    parts = []
    if latest and latest != version:
        parts.append(
            f"> **A newer version is available:** [DoubleClick Fixer {latest}]"
            f"(https://github.com/{repo}/releases/latest). Download that one instead.\n"
        )
    parts.append(template.replace("__VERSION__", version).replace("__REPO__", repo).strip())
    parts.append(f"## What's new\n\n{unwrap(changelog_section(version, changelog))}")
    previous = previous_version(version, changelog)
    if previous:
        parts.append(f"**All changes:** https://github.com/{repo}/compare/v{previous}...v{version}")
    return "\n\n".join(parts) + "\n"


if __name__ == "__main__":
    arguments = sys.argv[1:]
    latest = None
    if "--latest" in arguments:
        index = arguments.index("--latest")
        latest = arguments[index + 1]
        del arguments[index : index + 2]
    if len(arguments) != 2:
        raise SystemExit(__doc__)
    sys.stdout.write(notes(arguments[0], arguments[1], latest))
