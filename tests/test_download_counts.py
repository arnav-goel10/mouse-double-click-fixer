"""tools/download_counts.py: installer downloads without the two old
installers CI's install test fetches, read from a stand-in for the API."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import unittest

from tools import download_counts, old_installers

BASE = "https://github.com/arnav-goel10/mouse-double-click-fixer/releases/download"


def asset(tag: str, name: str, downloads: int) -> dict:
    return {"name": name, "download_count": downloads, "browser_download_url": f"{BASE}/{tag}/{name}"}


LISTING = [
    {"tag_name": "v1.1.0", "draft": False, "assets": [
        asset("v1.1.0", "DoubleClickFixer-Setup.exe", 8),
        asset("v1.1.0", "DoubleClickFixer.dmg", 7),
        asset("v1.1.0", "SHA256SUMS.txt", 4),
    ]},
    {"tag_name": "v1.2.0", "draft": True, "assets": [asset("v1.2.0", "DoubleClickFixer-Setup.exe", 3)]},
    {"tag_name": "v0.5.3", "draft": False, "assets": [
        asset("v0.5.3", "DoubleClickFixer-Setup.exe", 81),
        asset("v0.5.3", "DoubleClickFixer-macos.zip", 1),
    ]},
    {"tag_name": "v0.2.6", "draft": False, "assets": [asset("v0.2.6", "DoubleClickFixer-Setup.exe", 107)]},
]


class CountTests(unittest.TestCase):
    def test_the_files_ci_fetches_are_the_ones_the_manifest_lists(self) -> None:
        self.assertEqual(download_counts.ci_urls(), {installer.url for installer in old_installers.load()})
        self.assertEqual(len(download_counts.ci_urls()), 2)

    def test_ci_files_drafts_and_checksums_are_left_out_of_the_total(self) -> None:
        report = download_counts.count(LISTING, download_counts.ci_urls())
        self.assertEqual(report["total"], 8 + 7 + 81 + 1 + 107)
        self.assertEqual(report["without_ci"], 8 + 7 + 1)
        self.assertEqual([row["tag"] for row in report["files"] if row["ci"]], ["v0.5.3", "v0.2.6"])
        self.assertNotIn("SHA256SUMS.txt", [row["name"] for row in report["files"]])
        self.assertNotIn("v1.2.0", [row["tag"] for row in report["files"]])

    def test_every_page_is_read(self) -> None:
        pages = {1: LISTING[:2], 2: LISTING[2:], 3: []}
        asked = []

        def get(url: str, token: str) -> object:
            asked.append(url)
            return pages[int(url.rsplit("page=", 1)[1])]

        self.assertEqual(download_counts.releases("o/r", "", get=get), LISTING)
        self.assertEqual(len(asked), 3)
        self.assertTrue(all(url.startswith("https://api.github.com/repos/o/r/releases?per_page=100") for url in asked))


if __name__ == "__main__":
    unittest.main()
