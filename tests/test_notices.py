"""Third-party notices: the generator, the committed copy, and finding the
file in a build."""

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app import notices

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import make_notices  # noqa: E402

COMMITTED = ROOT / "THIRD_PARTY_NOTICES.md"


def pinned(name: str) -> str:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    return re.search(rf"^{re.escape(name)}==([^\s;]+)", requirements, re.MULTILINE).group(1)


class GeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = make_notices.render()

    def test_it_names_the_lgpl_libraries_with_their_exact_versions_and_sources(self) -> None:
        from PySide6 import QtCore

        qt = QtCore.qVersion()
        self.assertIn(f"| Qt | {qt} | LGPL-3.0-only |", self.text)
        self.assertIn(f"qt-everywhere-src-{qt}.tar.xz", self.text)
        self.assertIn("pyside-setup-everywhere-src-", self.text)
        self.assertIn("Copyright (C) The Qt Company Ltd.", self.text)
        self.assertIn("/licenses-used-in-qt.html", self.text)

    def test_the_lgpl_comes_with_the_gpl_it_builds_on(self) -> None:
        self.assertIn("GNU LESSER GENERAL PUBLIC LICENSE\n                       Version 3, 29 June 2007", self.text)
        self.assertIn("GNU GENERAL PUBLIC LICENSE\n                       Version 3, 29 June 2007", self.text)
        self.assertIn("END OF TERMS AND CONDITIONS", self.text)

    def test_python_and_its_licence_are_included(self) -> None:
        import platform

        self.assertIn(f"| Python | {platform.python_version()} | PSF-2.0 |", self.text)
        self.assertIn("PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2", self.text)

    def test_every_licence_link_points_at_a_heading_in_the_file(self) -> None:
        headings = {make_notices._anchor(line[4:]) for line in self.text.splitlines() if line.startswith("### ")}
        links = set(re.findall(r"\]\(#([^)]+)\)", self.text))
        self.assertTrue(links)
        self.assertLessEqual(links, headings)

    def test_pyobjc_is_listed_where_it_is_installed(self) -> None:
        if make_notices.installed("pyobjc-core"):
            self.assertIn("## PyObjC (macOS)", self.text)
            self.assertIn("Ronald Oussoren", self.text)
        else:
            self.assertNotIn("PyObjC", self.text)

    def test_the_windows_installer_tool_is_credited(self) -> None:
        self.assertIn("Inno Setup", self.text)

    def test_writes_and_checks_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "notices" / "THIRD_PARTY_NOTICES.md"
            with mock.patch("builtins.print"):
                self.assertEqual(make_notices.main(["--output", str(output)]), 0)
                self.assertEqual(make_notices.main(["--output", str(output), "--check"]), 0)
            output.write_text("stale", encoding="utf-8")
            with mock.patch("sys.stderr"):
                self.assertEqual(make_notices.main(["--output", str(output), "--check"]), 1)


class CommittedCopyTests(unittest.TestCase):
    """The copy in the repository follows requirements.txt; regenerate it
    with `python tools/make_notices.py` after a dependency update."""

    def test_it_matches_the_pinned_dependencies(self) -> None:
        text = COMMITTED.read_text(encoding="utf-8")
        self.assertIn(f"| Qt for Python: PySide6 | {pinned('PySide6-Essentials')} |", text)
        for name in ("pyobjc-framework-Quartz", "pyobjc-framework-Cocoa"):
            self.assertIn(f"| {name} | {pinned(name)} |", text)

    def test_every_build_ships_it(self) -> None:
        spec = (ROOT / "doubleclick-fixer.spec").read_text(encoding="utf-8")
        self.assertIn('"tools/make_notices.py", "--output", str(NOTICES)', spec)
        self.assertIn("datas=DATAS", spec)


class ResolverTests(unittest.TestCase):
    def test_a_source_checkout_finds_the_repository_copy(self) -> None:
        self.assertEqual(notices.notices_path(), COMMITTED.resolve())

    def test_a_built_app_finds_its_own_copy(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            contents = Path(folder) / "DoubleClick Fixer.app" / "Contents"
            frameworks = contents / "Frameworks"
            resources = contents / "Resources"
            frameworks.mkdir(parents=True)
            resources.mkdir()
            (contents / "MacOS").mkdir()
            executable = contents / "MacOS" / "DoubleClickFixer"
            with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(
                sys, "_MEIPASS", str(frameworks), create=True
            ), mock.patch.object(sys, "executable", str(executable)), mock.patch.object(
                notices, "__file__", str(frameworks / "app" / "notices.pyc")
            ):
                self.assertIsNone(notices.notices_path())
                # macOS: the file is in Resources (PyInstaller links it into Frameworks too).
                (resources / notices.NOTICES_FILE).write_text("notices", encoding="utf-8")
                self.assertEqual(notices.notices_path(), (resources / notices.NOTICES_FILE).resolve())
                # Windows: beside the bundled program files.
                (frameworks / notices.NOTICES_FILE).write_text("notices", encoding="utf-8")
                self.assertEqual(notices.notices_path(), (frameworks / notices.NOTICES_FILE).resolve())


if __name__ == "__main__":
    unittest.main()
