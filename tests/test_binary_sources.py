"""tools/binary_sources.py: a build fails if it collects a binary from
anywhere but Python itself and its packages."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import importlib.util
import os
import shutil
import sys
import sysconfig
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("binary_sources", ROOT / "tools" / "binary_sources.py")
binary_sources = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(binary_sources)


class BinarySourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.python = self.root / "python"
        self.packages = self.root / "venv" / "site-packages"
        self.elsewhere = self.root / "Program Files" / "MySQL" / "bin"
        for folder in (self.python / "DLLs", self.packages / "PySide6", self.elsewhere):
            folder.mkdir(parents=True)
        self.roots = [("Python", binary_sources._normal(str(self.python))),
                      ("site-packages", binary_sources._normal(str(self.packages)))]

    def file(self, path: Path, data: bytes = b"MZ\x90\x00") -> str:
        path.write_bytes(data)
        return str(path)

    def test_binaries_from_python_and_its_packages_pass(self) -> None:
        entries = [
            ("python313.dll", self.file(self.python / "python313.dll"), "BINARY"),
            ("_ssl.pyd", self.file(self.python / "DLLs" / "_ssl.pyd"), "EXTENSION"),
            ("PySide6/Qt6Core.dll", self.file(self.packages / "PySide6" / "Qt6Core.dll"), "BINARY"),
            # A data file anywhere is not code, whatever its source.
            ("notes.txt", self.file(self.elsewhere / "notes.txt", b"text"), "DATA"),
        ]
        self.assertEqual(binary_sources.foreign(entries, self.roots), [])
        summary = binary_sources.check(entries, self.roots)
        self.assertTrue(summary.startswith("Binaries from outside Python and its packages: none (3 checked"), summary)

    def test_a_binary_from_anywhere_else_fails_the_build_naming_its_source(self) -> None:
        leaked = self.file(self.elsewhere / "libssl-3-x64.dll")
        (self.root / "jdk").mkdir()
        ucrt = self.file(self.root / "jdk" / "ucrtbase.dll")
        entries = [
            ("python313.dll", self.file(self.python / "python313.dll"), "BINARY"),
            ("libssl-3-x64.dll", leaked, "BINARY"),
            ("ucrtbase.dll", ucrt, "BINARY"),
        ]
        self.assertEqual(binary_sources.foreign(entries, self.roots),
                         sorted([("libssl-3-x64.dll", leaked), ("ucrtbase.dll", ucrt)]))
        with self.assertRaises(SystemExit) as raised:
            binary_sources.check(entries, self.roots)
        message = str(raised.exception)
        self.assertIn("collects 2 binaries from outside Python and its packages", message)
        self.assertIn(f"libssl-3-x64.dll  (from {leaked})", message)
        self.assertIn(f"ucrtbase.dll  (from {ucrt})", message)

    def test_code_is_recognised_by_type_name_or_first_bytes(self) -> None:
        for name, data, typecode in (
            ("plugin.dll", b"", "DATA"),  # by name, whatever PyInstaller calls it
            ("libfoo.dylib", b"", "DATA"),
            ("_ext.so", b"", "DATA"),
            ("QtCore", bytes.fromhex("cffaedfe"), "DATA"),  # a framework's binary has no suffix
            ("Universal", bytes.fromhex("cafebabe"), "DATA"),
            ("renamed.bin", b"MZ\x90\x00", "DATA"),
            ("anything", b"text", "BINARY"),
        ):
            with self.subTest(name=name):
                source = self.file(self.elsewhere / name, data)
                self.assertEqual(binary_sources.foreign([(name, source, typecode)], self.roots), [(name, source)])
        text = self.file(self.elsewhere / "README", b"text")
        self.assertEqual(binary_sources.foreign([("README", text, "DATA")], self.roots), [])

    def test_links_pyinstaller_makes_inside_the_build_are_not_sources(self) -> None:
        # Their "source" is where they point, relative to themselves; what
        # they point to is an entry of its own.
        entries = [
            ("libpyside6.abi3.6.11.dylib", "PySide6/libpyside6.abi3.6.11.dylib", "SYMLINK"),
            ("PySide6/Qt/lib/QtCore.framework/QtCore", "Versions/Current/QtCore", "SYMLINK"),
            ("PySide6/libpyside6.abi3.6.11.dylib", self.file(self.packages / "PySide6" / "libpyside6.abi3.6.11.dylib"),
             "BINARY"),
        ]
        self.assertEqual(binary_sources.foreign(entries, self.roots), [])
        self.assertIn("(1 checked", binary_sources.check(entries, self.roots))

    def test_a_neighbouring_folder_is_not_inside(self) -> None:
        neighbour = self.root / "python-evil"
        neighbour.mkdir()
        source = self.file(neighbour / "python313.dll")
        self.assertEqual(binary_sources.foreign([("python313.dll", source, "BINARY")], self.roots),
                         [("python313.dll", source)])

    @unittest.skipIf(sys.platform == "win32", "symbolic links need privileges on Windows")
    def test_a_link_counts_where_it_leads(self) -> None:
        real = self.file(self.elsewhere / "libcrypto.3.dylib")
        link = self.python / "libcrypto.3.dylib"
        os.symlink(real, link)
        self.assertEqual(binary_sources.foreign([("libcrypto.3.dylib", str(link), "BINARY")], self.roots),
                         [("libcrypto.3.dylib", str(link))])

    def test_the_roots_are_python_and_this_environments_site_packages(self) -> None:
        roots = dict((folder, what) for what, folder in binary_sources.allowed_roots())
        self.assertEqual(roots[binary_sources._normal(sys.base_prefix)], "Python")
        self.assertEqual(roots[binary_sources._normal(sysconfig.get_paths()["platlib"])], "site-packages")
        self.assertNotIn(binary_sources._normal(os.path.expanduser("~")), roots)

    def test_the_spec_checks_what_it_bundles_after_leaving_things_out(self) -> None:
        spec = (ROOT / "doubleclick-fixer.spec").read_text(encoding="utf-8")
        check = spec.index("print(binary_sources.check(analysis.binaries + analysis.datas))")
        self.assertLess(spec.index("analysis.datas = [entry for entry in analysis.datas if not "
                                   "make_notices.unused_qt_file(entry[0])]"), check)
        self.assertLess(check, spec.index("make_notices.write(NOTICES"))
        self.assertLess(check, spec.index("pyz = PYZ("))


if __name__ == "__main__":
    unittest.main()
