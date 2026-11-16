"""Installer downloads from the project's releases, without CI's own.

CI's install test upgrades from the 0.2.6 and 0.5.3 installers
(tools/old_installers.json). Until they were cached, and later kept as an
artifact, every run downloaded them from their release pages, so those two
files' counts are mostly CI and say little about use. This reads every
published release from GitHub's API (which counts no download), and reports
each installer file's count and a total that leaves those two out.

    python tools/download_counts.py            # a table
    python tools/download_counts.py --json     # the same, as JSON

GITHUB_TOKEN, if set, is sent with the requests, for a higher rate limit.
Read the numbers on the day you quote them: they only go up.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from typing import Callable, Iterable, Optional, Sequence

try:
    from tools import old_installers
except ImportError:  # run as tools/download_counts.py
    import old_installers  # type: ignore[no-redef]

REPOSITORY = "arnav-goel10/mouse-double-click-fixer"
API = "https://api.github.com"
#: The files a person downloads to run the app: the Windows installer and
#: portable exe, the macOS disk image, and the macOS archive the app updates
#: itself from. Checksums, signatures and notices are left out.
INSTALLERS = ("DoubleClickFixer-Setup.exe", "DoubleClickFixer.exe", "DoubleClickFixer.dmg", "DoubleClickFixer-macos.zip")


def ci_urls() -> set[str]:
    """The release files CI's install test fetches."""
    return {installer.url for installer in old_installers.load()}


def get_json(url: str, token: str = "") -> object:
    headers = {"User-Agent": old_installers.USER_AGENT, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
        return json.load(response)


def releases(repository: str = REPOSITORY, token: str = "",
             get: Callable[[str, str], object] = get_json) -> list[dict]:
    """Every release of `repository`, newest first, drafts included."""
    found: list[dict] = []
    page = 1
    while True:
        batch = get(f"{API}/repos/{repository}/releases?per_page=100&page={page}", token)
        if not isinstance(batch, list) or not batch:
            return found
        found.extend(batch)
        page += 1


def count(listing: Iterable[dict], excluded: set[str]) -> dict:
    """Each published release's installer files and their download counts,
    and the totals with and without the `excluded` URLs."""
    rows = []
    total = without = 0
    for release in listing:
        if release.get("draft"):
            continue
        for asset in release.get("assets", []):
            if asset.get("name") not in INSTALLERS:
                continue
            downloads = int(asset.get("download_count", 0))
            ci = asset.get("browser_download_url") in excluded
            rows.append({"tag": release.get("tag_name"), "name": asset["name"], "downloads": downloads, "ci": ci})
            total += downloads
            if not ci:
                without += downloads
    return {"files": rows, "total": total, "without_ci": without}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repository", default=REPOSITORY)
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    arguments = parser.parse_args(argv)
    report = count(releases(arguments.repository, os.environ.get("GITHUB_TOKEN", "")), ci_urls())
    if arguments.json:
        print(json.dumps(report, indent=2))
        return 0
    for row in report["files"]:
        note = "  (CI's install test fetches this one; left out)" if row["ci"] else ""
        print(f"{row['tag']:>10}  {row['name']:<28} {row['downloads']:>6}{note}")
    print(f"\ninstaller downloads, all published releases: {report['total']}")
    print(f"without the files CI fetches:                {report['without_ci']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
