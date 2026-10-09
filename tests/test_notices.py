"""Third-party notices: the generator, the committed copy, finding the file in
a build, and the General pane's Acknowledgements button."""

try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import os
import re
import sys
import tempfile
import unittest
import zipfile
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
        # The Qt Company's server keeps every release in its archive.
        self.assertIn(f"https://download.qt.io/archive/qt/{'.'.join(qt.split('.')[:2])}/{qt}/single/", self.text)

    def test_the_code_inside_qt_is_found_in_its_libraries_and_attributed(self) -> None:
        build = make_notices.Build.from_environment()
        if build.qt("Gui") is None:
            self.skipTest("no Qt GUI library in this environment")
        for library, line in (
            ("Gui", "- FreeType"), ("Gui", "- HarfBuzz"), ("Gui", "- libpng 1."), ("Core", "- PCRE2 10."),
            ("Core", "- double-conversion, in Qt Core"),
            ("Network", "- The Public Suffix List, in Qt Network: MPL-2.0"),
            ("DBus", "- libdbus-1 headers, in Qt D-Bus: AFL-2.1"),
        ):
            data = build.qt(library)
            if data is not None and line not in self.text:
                # What the library has that looks like it, to go by.
                word = line[2:].split(",")[0].split()[0].encode()
                evidence = sorted(set(re.findall(rb"[ -~]{0,40}" + re.escape(word) + rb"[ -~]{0,40}", data)))[:12]
                versions = sorted(set(re.findall(rb"(?<=\x00)\d{1,2}\.\d{1,2}\.\d{1,3}(?=\x00)", data)))
                self.fail(f"no {line!r} in the notices; {library} has {evidence}, bare versions {versions}")
        # The FreeType License asks for this sentence in the documentation.
        self.assertIn("based in part on the work of the FreeType Team", self.text)
        for heading in ("The FreeType Project License", "Mozilla Public License, version 2.0", "Unicode License v3"):
            self.assertIn(f"### {heading}", self.text)
        self.assertIn("The FreeType Project LICENSE", self.text)
        self.assertIn("Mozilla Public License Version 2.0", self.text)

    def test_the_code_inside_python_is_attributed(self) -> None:
        for name in ("SipHash24", "dtoa and strtod", "Mersenne Twister", "HACL*"):
            self.assertIn(f"- {name}", self.text)
        self.assertIn("Copyright (C) 1997 - 2002, Makoto Matsumoto and Takuji Nishimura", self.text)

    def test_the_windows_shapes_and_the_source_are_explained_without_a_contact_address(self) -> None:
        self.assertIn("in the `_internal` folder of the program folder", self.text)
        self.assertIn("rebuild it with your own Qt or Qt for Python", self.text)
        self.assertIn("whose download server keeps every release", self.text)
        self.assertNotIn("open an issue", self.text)
        self.assertNotIn("@", self.text.split("## Licence texts")[0].replace("@rpath", ""))

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
        self.assertIn(
            "make_notices.write(NOTICES, make_notices.Build.from_toc(analysis.binaries, analysis.pure, "
            "analysis.datas))", spec,
        )
        self.assertIn('analysis.datas.append(("THIRD_PARTY_NOTICES.md", str(NOTICES.resolve()), "DATA"))', spec)

    def test_every_build_leaves_out_the_plugins_the_app_never_uses(self) -> None:
        spec = (ROOT / "doubleclick-fixer.spec").read_text(encoding="utf-8")
        filtering = spec.index("make_notices.unused_qt_file(entry[0])")
        self.assertLess(filtering, spec.index("make_notices.write(NOTICES"), "left out before the notices are made")

    def test_the_macos_build_checks_the_notices_against_the_signed_app(self) -> None:
        script = (ROOT / "installer" / "build_macos.sh").read_text(encoding="utf-8")
        self.assertIn('python3 tools/make_notices.py --bundle "$signed" --check --output '
                      '"$signed/Contents/Resources/THIRD_PARTY_NOTICES.md"', script)


def fake_binary(folder: Path, name: str, *contents: bytes) -> Path:
    path = folder / name
    path.write_bytes(bytes.fromhex("cffaedfe") + b"\x00".join(contents))
    return path


class BuildScanTests(unittest.TestCase):
    """What the notices list follows the files a build ships."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def build(self, files, modules=(), platform="darwin"):
        return make_notices.Build({path.name: path for path in files}, set(modules), platform)

    def names(self, items):
        return {item.name for item in items}

    def test_qt_code_is_listed_only_where_its_marks_are(self) -> None:
        gui = fake_binary(self.folder, "QtGui", b"FT_New_Face failed", b"libpng version 1.6.99")
        found = make_notices.inside_qt(self.build([gui]))
        self.assertIn("FreeType", self.names(found))
        self.assertEqual([item.version for item in found if item.name == "libpng"], ["1.6.99"])
        # Windows' Qt GUI keeps only libpng's version check and its bare
        # version string (zlib's, which it also names, is not 1.6).
        windows = fake_binary(self.folder, "Qt6Gui.dll", b"Application built with libpng-", b" but running with ",
                              b"1.3.1", b"1.6.58", b"PNG: Compression %d out of range")
        found = make_notices.inside_qt(self.build([windows], platform="win32"))
        self.assertEqual([item.version for item in found if item.name == "libpng"], ["1.6.58"])
        unsure = fake_binary(self.folder, "Qt6Gui.dll", b"Application built with libpng-", b"1.6.57", b"1.6.58", b"")
        found = make_notices.inside_qt(self.build([unsure], platform="win32"))
        self.assertEqual([item.version for item in found if item.name == "libpng"], [""])
        self.assertNotIn("HarfBuzz", self.names(found))  # no mark of it
        self.assertNotIn("PCRE2", self.names(found))  # no Qt Core at all
        self.assertIn("Smooth scaling algorithm", self.names(found))  # always part of Qt GUI

    def test_platform_only_code_is_listed_on_its_platform(self) -> None:
        core = fake_binary(self.folder, "QtCore", b"QCborStreamReader")
        gui = fake_binary(self.folder, "Qt6Gui.dll", b"nothing")
        self.assertIn("forkfd", self.names(make_notices.inside_qt(self.build([core]))))
        self.assertNotIn("forkfd", self.names(make_notices.inside_qt(self.build([core], platform="win32"))))
        windows = self.names(make_notices.inside_qt(self.build([gui], platform="win32")))
        self.assertIn("D3D12 Memory Allocator", windows)
        self.assertNotIn("zlib", self.names(make_notices.inside_qt(self.build([core]))))  # the system's
        windows_core = fake_binary(self.folder, "Qt6Core.dll", b"QCborStreamReader")
        self.assertIn("zlib", self.names(make_notices.inside_qt(self.build([windows_core], platform="win32"))))
        self.assertNotIn("D3D12 Memory Allocator", self.names(make_notices.inside_qt(self.build([gui]))))

    def test_code_it_has_no_notice_for_fails_the_build(self) -> None:
        plugin = fake_binary(self.folder, "libqjpeg.dylib", b"libjpeg-turbo version 3.2.0 (build )")
        with self.assertRaisesRegex(SystemExit, "libjpeg-turbo"):
            make_notices.inside_qt(self.build([plugin]))

    def test_mesa_software_opengl_fails_the_build(self) -> None:
        # PySide6's opengl32sw.dll, as llvmpipe names itself to OpenGL.
        mesa = fake_binary(self.folder, "opengl32sw.dll", b"llvmpipe (LLVM 15.0.7, 256 bits)")
        with self.assertRaisesRegex(SystemExit, r"Mesa .*opengl32sw\.dll"):
            make_notices.inside_qt(self.build([mesa], platform="win32"))
        gui = fake_binary(self.folder, "Qt6Gui.dll", b"llvmpipe", b"Mesa")  # Qt naming it is not Mesa itself
        make_notices.inside_qt(self.build([gui], platform="win32"))

    def windows_openssl(self, qt_version: bytes = b"OpenSSL 3.5.5 27 Jan 2026") -> list:
        """A Windows build's two copies: Python's, which its modules link to,
        and the one Qt's OpenSSL backend loads by name."""
        return [
            fake_binary(self.folder, "libcrypto-3.dll", b"OpenSSL 3.0.15 3 Sep 2024", b"x"),
            fake_binary(self.folder, "libssl-3.dll", b"no version text in libssl"),
            fake_binary(self.folder, "_hashlib.pyd", b"LIBCRYPTO-3.dll"),
            fake_binary(self.folder, "_ssl.pyd", b"libssl-3.dll", b"libcrypto-3.dll"),
            fake_binary(self.folder, "libcrypto-3-x64.dll", qt_version, b"x"),
            fake_binary(self.folder, "libssl-3-x64.dll"),
            fake_binary(self.folder, "qopensslbackend.dll", b"libssl-3-x64", b"libcrypto-3-x64", b"libssl-3", b""),
        ]

    def test_each_openssl_copy_is_listed_with_the_version_its_file_carries(self) -> None:
        build = self.build(self.windows_openssl(), platform="win32")
        self.assertEqual(
            [(item.name, item.version) for item in make_notices.python_libraries(build) if "OpenSSL" in item.name],
            [("OpenSSL (libcrypto, libssl)", "3.0.15")],
        )
        self.assertEqual(
            [(item.name, item.version, item.licence) for item in make_notices.qt_libraries(build)],
            [("OpenSSL for Qt Network (libcrypto-3-x64.dll, libssl-3-x64.dll)", "3.5.5", "Apache-2.0")],
        )
        text = make_notices.render(build)
        self.assertIn("| OpenSSL (libcrypto, libssl) | 3.0.15 | Apache-2.0 |", text)
        self.assertIn("| OpenSSL for Qt Network (libcrypto-3-x64.dll, libssl-3-x64.dll) | 3.5.5 | Apache-2.0 |", text)
        self.assertIn("openssl-3.5.5/openssl-3.5.5.tar.gz", text)
        self.assertIn("### OpenSSL for Qt Network", text)

    def test_an_openssl_copy_without_a_readable_version_fails_the_build(self) -> None:
        build = self.build(self.windows_openssl(b"nothing to go by"), platform="win32")
        with self.assertRaisesRegex(SystemExit, "Couldn't read which OpenSSL libcrypto-3-x64.dll is"):
            make_notices.qt_libraries(build)
        build = self.build(self.windows_openssl(b"OpenSSL 1.1.1w  11 Sep 2023"), platform="win32")
        with self.assertRaisesRegex(SystemExit, "OpenSSL 1.1.1w"):
            make_notices.qt_libraries(build)

    def test_an_openssl_copy_neither_python_nor_qt_loads_fails_the_build(self) -> None:
        files = [path for path in self.windows_openssl() if path.name != "qopensslbackend.dll"]
        with self.assertRaisesRegex(SystemExit, "ships libcrypto-3-x64.dll, which neither"):
            make_notices.python_libraries(self.build(files, platform="win32"))

    def test_python_notices_follow_the_modules_that_ship(self) -> None:
        select = fake_binary(self.folder, "select.cpython-314-darwin.so", b"kqueue")
        random = fake_binary(self.folder, "_random.cpython-314-darwin.so")
        found = self.names(make_notices.inside_python(self.build([select, random], {"encodings.uu_codec"})))
        self.assertLessEqual({"select.kqueue", "Mersenne Twister (random)", "uu codec", "SipHash24"}, found)
        if not {"_sha2", "_md5", "_sha1", "_sha3", "_blake2", "_hmac"} & set(sys.builtin_module_names):
            self.assertNotIn("HACL* (hashlib's MD5, SHA-1, SHA-2, SHA-3, BLAKE2 and HMAC)", found)
        bare = self.names(make_notices.inside_python(self.build([])))
        self.assertNotIn("select.kqueue", bare)
        with self.assertRaisesRegex(SystemExit, "asyncio"):
            make_notices.inside_python(self.build([], {"asyncio"}))

    def test_a_library_the_system_provides_is_not_listed(self) -> None:
        system = fake_binary(self.folder, "pyexpat.cpython-314-darwin.so", b"/usr/lib/libexpat.1.dylib")
        self.assertNotIn("Expat", {item.name for item in make_notices.python_libraries(self.build([system]))})
        own = fake_binary(self.folder, "pyexpat.pyd", b"expat compiled in")
        windows = make_notices.python_libraries(self.build([own], platform="win32"))
        self.assertIn("Expat", {item.name for item in windows})
        zlib = fake_binary(self.folder, "zlib.cpython-314-darwin.so", b"/usr/lib/libz.1.dylib")
        zstd = fake_binary(self.folder, "_zstd.cpython-314-darwin.so", b"@rpath/libzstd.1.dylib")
        listed = {item.name for item in make_notices.python_libraries(self.build([zlib, zstd]))}
        self.assertNotIn("zlib", listed)
        if sys.version_info >= (3, 14):
            self.assertIn("Zstandard (libzstd)", listed)

    def test_a_finished_build_is_read_from_its_files(self) -> None:
        app = self.folder / "Example.app" / "Contents"
        (app / "Frameworks").mkdir(parents=True)
        (app / "Resources").mkdir()
        fake_binary(app / "Frameworks", "QtNetwork", b"qIsEffectiveTLD")
        (app / "Frameworks" / "notes.txt").write_text("not a binary")
        with zipfile.ZipFile(app / "Resources" / "base_library.zip", "w") as archive:
            archive.writestr("encodings/uu_codec.pyc", b"")
            archive.writestr("encodings/__init__.pyc", b"")
        build = make_notices.Build.from_bundle(self.folder / "Example.app")
        self.assertEqual(set(build.files), {"QtNetwork"})
        self.assertLessEqual({"encodings", "encodings.uu_codec"}, build.modules)
        self.assertIn("The Public Suffix List", self.names(make_notices.inside_qt(build)))

    def test_the_unused_plugins_and_their_library_are_recognised(self) -> None:
        for destination in (
            "PySide6/Qt/plugins/imageformats/libqjpeg.dylib", "PySide6/plugins/imageformats/qtiff.dll",
            "PySide6/Qt/plugins/iconengines/libqsvgicon.dylib", "PySide6/Qt/lib/QtSvg.framework/Versions/A/QtSvg",
            "PySide6/Qt/lib/QtSvg.framework/Resources/Info.plist", "QtSvg", "PySide6/Qt6Svg.dll",
            "PySide6/QtSvg.abi3.so", "PySide6/opengl32sw.dll", "opengl32sw.dll",
        ):
            self.assertTrue(make_notices.unused_qt_file(destination), destination)
        for destination in (
            "PySide6/Qt/plugins/imageformats/libqico.dylib", "PySide6/Qt/plugins/platforms/libqcocoa.dylib",
            "PySide6/Qt/lib/QtGui.framework/Versions/A/QtGui", "QtGui", "PySide6/Qt6Gui.dll",
        ):
            self.assertFalse(make_notices.unused_qt_file(destination), destination)

    def test_every_licence_text_it_can_use_is_vendored(self) -> None:
        from_environment = {"PSF-2.0", "MIT-PyObjC"}
        keys = {rule.inside.text for rule in make_notices.QT_RULES if rule.inside.text}
        keys |= set(make_notices.TEXTS) - from_environment
        for key in keys:
            self.assertIn(key, make_notices.TEXTS)
            path = make_notices.LICENSES / f"{key}.txt"
            self.assertTrue(path.is_file(), path)
            self.assertGreater(len(path.read_text(encoding="utf-8").strip()), 100, path)


class EnvironmentFilesTests(unittest.TestCase):
    """The markers and version strings, against this environment's real
    files where it has them (PySide6's Windows wheels do)."""

    def pyside6_file(self, name: str) -> Path:
        import PySide6

        path = Path(PySide6.__file__).parent / name
        if not path.is_file():
            self.skipTest(f"PySide6 has no {name} here")
        return path

    def test_the_mesa_marker_is_in_pyside6s_software_opengl(self) -> None:
        path = self.pyside6_file("opengl32sw.dll")
        data = path.read_bytes()
        markers = [marker for marker, name in make_notices.UNATTRIBUTED.items() if name.startswith("Mesa")]
        context = [data[max(0, index - 40):index + 60] for index in
                   (data.find(b"llvmpipe"), data.find(b"Mesa ")) if index >= 0]
        self.assertTrue(any(marker in data for marker in markers), f"none of {markers} in {path}: {context}")
        self.assertTrue(make_notices.unused_qt_file(str(path.relative_to(path.parent.parent))))

    def test_each_openssl_copy_here_says_its_version(self) -> None:
        import ssl

        build = make_notices.Build.from_environment()
        copies = make_notices.openssl_copies(build)
        if not copies:
            self.skipTest("no OpenSSL files among what a build here collects")
        text = make_notices.render(build)
        for copy in copies:
            self.assertRegex(copy.version, r"^3\.\d+\.\d+", copy)
            if copy.for_qt:
                files = ", ".join(name for name in (copy.crypto, copy.ssl) if name)
                self.assertIn(f"| OpenSSL for Qt Network ({files}) | {copy.version} |", text)
            else:
                self.assertEqual(copy.version, ssl.OPENSSL_VERSION.split()[1], "Python's copy is the one ssl runs")
                self.assertIn(f"| OpenSSL (libcrypto, libssl) | {copy.version} |", text)


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


class AcknowledgementsButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from app import settings

        directory = Path(tempfile.mkdtemp())
        for patcher in (
            mock.patch.object(settings, "config_dir", return_value=directory),
            mock.patch.object(settings, "LEGACY_PATH", directory / "absent.json"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        from app.controller import AppController
        from app.ui.window import GeneralPage

        self.page = GeneralPage(AppController())
        self.addCleanup(self.page.deleteLater)

    def test_general_has_an_acknowledgements_button(self) -> None:
        button = self.page.acknowledgements_button
        self.assertTrue(button.text().startswith("Acknowledgements"))
        self.assertTrue(self.page.isAncestorOf(button))
        self.assertTrue(button.isEnabled())

    def test_it_opens_the_notices_file(self) -> None:
        with mock.patch.object(notices, "open_file", return_value=True) as opened, mock.patch(
            "app.ui.window.QMessageBox.information"
        ) as message:
            self.page.acknowledgements_button.click()
        opened.assert_called_once_with(COMMITTED.resolve())
        message.assert_not_called()

    def test_it_says_where_the_file_is_when_nothing_opens_it(self) -> None:
        with mock.patch.object(notices, "open_file", return_value=False), mock.patch(
            "app.ui.window.QMessageBox.information"
        ) as message:
            self.page.acknowledgements_button.click()
        self.assertIn(str(COMMITTED.resolve()), message.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
