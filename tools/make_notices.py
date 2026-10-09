#!/usr/bin/env python3
"""Write THIRD_PARTY_NOTICES.md: the software DoubleClick Fixer ships that
others wrote, with exact versions, licences, copyright lines, where to get
the source, and the licence texts.

What it lists is read from the files a build ships. Qt and Python carry
third-party code compiled into their own libraries (FreeType and libpng in
Qt GUI, PCRE2 in Qt Core, HACL* in Python's hash modules, and so on); each
library and module is scanned for the strings and symbols that code leaves
behind, and whatever is found gets its notice and licence text here.

- The PyInstaller spec calls ``write()`` with the files and modules of the
  build it is making, after leaving out the Qt plugins the app never uses,
  and ships the result (Contents/Resources on macOS, next to the program
  files on Windows).
- ``--bundle PATH`` reads a finished build instead. installer/build_macos.sh
  runs ``--bundle <app> --check --output <its notices>`` on the signed app,
  so notices that don't match the app's own files fail the build.
- With neither, it looks at the Python environment that runs it. That is
  how the copy at the top of the repository is made (``python
  tools/make_notices.py``); the tests fail until it matches requirements.txt.

Versions come from the Python that runs this script and from the binaries
themselves, so run it with the environment that builds the app.
"""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import importlib.util
import os
import platform
import re
import subprocess
import sys
import sysconfig
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
LICENSES = Path(__file__).resolve().parent / "licenses"
DEFAULT_OUTPUT = ROOT / "THIRD_PARTY_NOTICES.md"

#: The Qt libraries the app uses, directly or through the ones it imports.
QT_LIBRARIES = ("Core", "Gui", "Widgets", "Network", "DBus")
#: The Qt plugin folders a build collects for those.
QT_PLUGIN_FOLDERS = ("platforms", "styles", "imageformats", "iconengines", "tls", "networkinformation", "generic")
#: Plugins the app has no use for, left out of every build (the spec reads
#: this): it draws its icons in code and reads and writes only PNG, which Qt
#: GUI has built in. Leaving them out keeps their code (libjpeg, libtiff,
#: libwebp and Qt SVG among it) out of the app altogether.
UNUSED_QT_PLUGINS = frozenset({
    "qgif", "qicns", "qjpeg", "qmacheif", "qmacjp2", "qpdf", "qsvg", "qsvgicon", "qtga", "qtiff", "qwbmp", "qwebp",
})
#: Qt libraries only those plugins need.
UNUSED_QT_LIBRARIES = frozenset({"Svg", "Pdf"})
#: Other files PySide6 ships that the app never loads: opengl32sw.dll is Mesa's
#: software OpenGL (llvmpipe), which Qt falls back on on Windows when asked for
#: OpenGL without a working driver. The app draws only widgets, which never ask.
UNUSED_FILES = frozenset({"opengl32sw.dll"})


@dataclass
class Component:
    name: str
    version: str
    licence: str  # SPDX identifier
    copyright: str
    source: str  # the source of exactly this version, where one exists
    text: str  # key into TEXTS: the licence text that goes with it


@dataclass(frozen=True)
class Inside:
    """Third-party code compiled into a Qt library or plugin, or into Python."""

    name: str
    within: str
    licence: str  # SPDX expression, as used here
    copyright: str
    text: str = ""  # key into TEXTS; empty where the licence sets no conditions
    note: str = ""  # anything the licence asks to be said, said here
    version: str = ""


#: Licence texts and their headings. Most are in tools/licenses/<key>.txt;
#: Python's and PyObjC's come from the build environment itself.
TEXTS: Dict[str, str] = {
    "LGPL-3.0": "GNU Lesser General Public License, version 3",
    "GPL-3.0": "GNU General Public License, version 3",
    "Apache-2.0": "Apache License, version 2.0",
    "PSF-2.0": "Python's licence (PSF License Agreement and history)",
    "MIT-PyObjC": "PyObjC's licence (MIT)",
    "mpdecimal": "mpdecimal's licence (BSD-2-Clause)",
    "zstd": "Zstandard's licence (BSD-3-Clause)",
    "xz-0BSD": "XZ Utils liblzma's licence (0BSD)",
    "expat": "Expat's licence (MIT)",
    "zlib": "zlib's licence",
    "libffi": "libffi's licence (MIT)",
    "bzip2": "bzip2's licence",
    # Inside Qt
    "FTL": "The FreeType Project License",
    "freetype-bdf": "FreeType's BDF driver licence (MIT)",
    "freetype-pcf": "FreeType's PCF driver licence (MIT and The Open Group)",
    "harfbuzz": "HarfBuzz's licence (Old MIT)",
    "libpng": "libpng's licence (PNG Reference Library License version 2)",
    "pcre2": "PCRE2's licence (BSD-3-Clause with the PCRE2 exception)",
    "sljit": "PCRE2's just-in-time compiler licence (BSD-2-Clause)",
    "double-conversion": "double-conversion's licence (BSD-3-Clause)",
    "easing": "Robert Penner's easing equations licence (BSD-3-Clause)",
    "rfc6234": "RFC 6234 SHA code licence (BSD-3-Clause)",
    "brg-endian": "brg_endian licence (BSD-2-Clause)",
    "qeventdispatcher-cf": "Qt's macOS event dispatcher licence (BSD-3-Clause)",
    "cocoa-platform-plugin": "Qt's Cocoa platform plugin licence (BSD-3-Clause)",
    "tinycbor": "TinyCBOR's licence (MIT)",
    "forkfd": "forkfd's licence (MIT)",
    "Unicode-3.0": "Unicode License v3",
    "aglfn": "Adobe Glyph List for New Fonts licence (BSD-3-Clause)",
    "md4c": "MD4C's licence (MIT)",
    "smooth-scaling": "Smooth scaling licences (BSD-2-Clause and Imlib2)",
    "webgradients": "WebGradients' licence (MIT)",
    "icc-srgb": "sRGB colour profile licence (International Color Consortium)",
    "opengl-headers": "OpenGL headers' licence (MIT)",
    "d3d12-memory-allocator": "D3D12 Memory Allocator's licence (MIT)",
    "d3d12-mipmap": "D3D12 mipmap generator's licence (MIT)",
    "vulkan-memory-allocator": "Vulkan Memory Allocator's licence (MIT)",
    "vulkan-registry": "Vulkan API Registry licence (MIT)",
    "MPL-2.0": "Mozilla Public License, version 2.0",
    "libpsl": "libpsl's licence (BSD-3-Clause)",
    "AFL-2.1": "Academic Free License, version 2.1",
    # Inside Python
    "python-mersenne-twister": "Mersenne Twister licence (BSD-3-Clause)",
    "python-uu": "uu codec notice",
    "python-kqueue": "select.kqueue licence (BSD-2-Clause)",
    "python-siphash": "SipHash24 licence (MIT)",
    "python-dtoa": "dtoa and strtod notice (David M. Gay)",
    "python-cfuhash": "cfuhash licence (BSD-3-Clause)",
    "python-mimalloc": "mimalloc's licence (MIT)",
    "python-gus": "Global Unbounded Sequences licence (BSD-2-Clause)",
    "python-hacl": "HACL*'s licence (MIT)",
    "python-zstd-bindings": "pyzstd's licence (BSD-3-Clause)",
}


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def app_version() -> str:
    return re.search(r'__version__ = "([^"]+)"', _read("app/__init__.py")).group(1)


def repository_url() -> str:
    return "https://github.com/" + re.search(r'REPOSITORY = "([^"]+)"', _read("app/updater.py")).group(1)


def installed(distribution: str) -> Optional[str]:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def licence_file(distribution: str, name: str) -> Optional[str]:
    try:
        files = metadata.distribution(distribution).files or []
    except metadata.PackageNotFoundError:
        return None
    for file in files:
        if file.name == name:
            return file.read_text(encoding="utf-8")
    return None


def python_licence() -> Optional[str]:
    """The LICENSE.txt Python installs: next to the standard library on macOS
    and Linux, in the installation folder on Windows (where it also covers
    the libraries that build includes)."""
    for folder in (sysconfig.get_path("stdlib"), sys.base_prefix):
        path = Path(folder) / "LICENSE.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    return None


def qt_version() -> str:
    from PySide6 import QtCore

    return QtCore.qVersion()


# -- what a build ships ---------------------------------------------------------------

@dataclass
class Build:
    """The binaries and Python modules one build of the app ships."""

    files: Dict[str, Path]  # file name -> the file
    modules: Set[str]
    platform: str = sys.platform
    _bytes: Dict[str, bytes] = field(default_factory=dict, repr=False)

    def data(self, name: str) -> Optional[bytes]:
        path = self.files.get(name)
        if path is None:
            return None
        if name not in self._bytes:
            self._bytes[name] = path.read_bytes()
        return self._bytes[name]

    def qt(self, library: str) -> Optional[bytes]:
        """A Qt library ("Core", "Gui", ...) or plugin ("qcocoa", ...)."""
        for name in (f"Qt{library}", f"Qt6{library}.dll", f"lib{library}.dylib", f"{library}.dll"):
            if name in self.files:
                return self.data(name)
        return None

    def extension(self, module: str) -> Optional[bytes]:
        """A Python extension module's file; b"" for one built into Python."""
        for name in self.files:
            if name.split(".")[0] == module and name.endswith((".so", ".pyd")):
                return self.data(name)
        return b"" if module in sys.builtin_module_names else None

    def bundles(self, module: str, system_library: str) -> bool:
        """Whether `module` ships with its own copy of a library rather than
        using the operating system's (macOS has zlib, Expat, libffi and bzip2
        in /usr/lib, and a Python may use those)."""
        data = self.extension(module)
        return data is not None and f"/usr/lib/{system_library}".encode() not in data

    def search(self, marker: bytes) -> List[str]:
        """The files that contain `marker`."""
        return sorted(name for name in self.files if marker in (self.data(name) or b""))

    @classmethod
    def from_toc(cls, binaries: Iterable[Sequence], pure: Iterable[Sequence], datas: Iterable[Sequence]) -> "Build":
        """From a PyInstaller Analysis' TOC lists, as the spec has them."""
        files = {}
        for destination, source, *_rest in binaries:
            if source and os.path.isfile(source):
                files[Path(destination).name] = Path(source)
        modules = {entry[0] for entry in pure}
        for destination, source, *_rest in datas:
            if Path(destination).name == "base_library.zip" and source and os.path.isfile(source):
                modules |= _zip_modules(Path(source))
        return cls(files, modules)

    @classmethod
    def from_bundle(cls, root: Path) -> "Build":
        """From a finished build: a macOS .app or a Windows program folder."""
        files: Dict[str, Path] = {}
        executables = []
        for folder, _directories, names in os.walk(root):
            for name in sorted(names):
                path = Path(folder) / name
                if path.is_symlink() or not path.is_file():
                    continue
                with open(path, "rb") as handle:
                    magic = handle.read(4)
                if magic[:2] == b"MZ" or magic in _MACHO_MAGICS:
                    files.setdefault(name, path)
                    if Path(folder).name == "MacOS" or name.lower().endswith(".exe"):
                        executables.append(path)
        modules: Set[str] = set()
        for path in Path(root).rglob("base_library.zip"):
            modules |= _zip_modules(path)
        for executable in executables:
            modules |= _pyz_modules(executable)
        return cls(files, modules)

    @classmethod
    def from_environment(cls) -> "Build":
        """What a build from this environment would ship, as far as can be
        told without making one."""
        files: Dict[str, Path] = {path.name: path for path in _qt_files_in_environment()}
        modules = set()
        for module in PYTHON_MODULES:
            try:
                spec = importlib.util.find_spec(module)
            except (ImportError, ValueError):
                spec = None
            if spec is None:
                continue
            modules.add(module)
            if spec.origin and spec.origin.endswith((".so", ".pyd")):
                files[Path(spec.origin).name] = Path(spec.origin)
        for path in _openssl_files_in_environment():
            files.setdefault(path.name, path)
        return cls(files, modules)


_MACHO_MAGICS = {bytes.fromhex(magic) for magic in ("cffaedfe", "cefaedfe", "cafebabe", "feedfacf", "feedface")}


def _zip_modules(path: Path) -> Set[str]:
    with zipfile.ZipFile(path) as archive:
        return {
            name[: -len(".pyc")].replace("/", ".").removesuffix(".__init__")
            for name in archive.namelist() if name.endswith(".pyc")
        }


def _pyz_modules(executable: Path) -> Set[str]:
    """The modules in a PyInstaller executable's PYZ archive (needs PyInstaller)."""
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError:
        return set()
    try:
        archive = CArchiveReader(str(executable))
    except Exception:  # noqa: BLE001 - not a PyInstaller executable
        return set()
    modules: Set[str] = set()
    for name in archive.toc:
        if name.endswith(".pyz"):
            modules |= set(archive.open_embedded_archive(name).toc)
    return modules


def _qt_files_in_environment() -> List[Path]:
    """The Qt libraries and plugins of this environment's PySide6 that a
    build collects."""
    spec = importlib.util.find_spec("PySide6")
    if spec is None or not spec.submodule_search_locations:
        return []
    package = Path(list(spec.submodule_search_locations)[0])
    found = []
    for library in QT_LIBRARIES:
        for candidate in (
            package / "Qt" / "lib" / f"Qt{library}.framework" / "Versions" / "A" / f"Qt{library}",
            package / f"Qt6{library}.dll",
        ):
            if candidate.is_file():
                found.append(candidate)
    for plugins in (package / "Qt" / "plugins", package / "plugins"):
        for folder in QT_PLUGIN_FOLDERS:
            for path in sorted((plugins / folder).glob("*")):
                if path.suffix in (".dylib", ".dll") and qt_plugin_name(path.name) not in UNUSED_QT_PLUGINS:
                    found.append(path)
    return found


def _openssl_files_in_environment() -> List[Path]:
    """OpenSSL's libraries where a build collects them from: beside Python's
    _ssl module (Windows keeps them there), inside PySide6, and on Windows the
    files Qt's OpenSSL backend loads by name, from the first folder on PATH
    that has them (PyInstaller's Qt Network hook collects them from there)."""
    folders = []
    for module in ("_ssl", "PySide6"):
        try:
            spec = importlib.util.find_spec(module)
        except (ImportError, ValueError):
            spec = None
        if spec is not None and spec.submodule_search_locations:
            folders += [(Path(folder), "**/") for folder in spec.submodule_search_locations]
        elif spec is not None and spec.origin and os.path.isfile(spec.origin):
            folders.append((Path(spec.origin).parent, ""))
    found = [
        path for folder, depth in folders for pattern in ("libcrypto*", "libssl*")
        for path in sorted(folder.glob(depth + pattern)) if _OPENSSL_FILE.match(path.name) and path.is_file()
    ]
    backend = [path for path in _qt_files_in_environment() if qt_plugin_name(path.name) == "qopensslbackend"]
    if sys.platform == "win32" and backend:
        for name in sorted({stem.decode("ascii") + ".dll" for stem in _QT_OPENSSL_NAME.findall(backend[0].read_bytes())}):
            folder = next((folder for folder in os.environ.get("PATH", "").split(os.pathsep)
                           if folder and os.path.isfile(os.path.join(folder, name))), None)
            if folder is not None:
                found.append(Path(folder) / name)
    return found


#: OpenSSL's libraries, as they are named on each platform: libcrypto-3.dll,
#: libcrypto-3-x64.dll, libcrypto.3.dylib, libssl.so.3 and so on.
_OPENSSL_FILE = re.compile(r"lib(crypto|ssl)([-.]|\.so\.)\d", re.IGNORECASE)
#: The OpenSSL files Qt's OpenSSL backend loads at run time, as it names them
#: ("libcrypto-3-x64" on 64-bit Windows).
_QT_OPENSSL_NAME = re.compile(rb"(?<![\w-])(lib(?:crypto|ssl)-[\w-]+)\x00")


def qt_plugin_name(file_name: str) -> str:
    """libqjpeg.dylib, qjpeg.dll -> qjpeg."""
    return file_name.split(".")[0].removeprefix("lib")


def unused_qt_file(destination: str) -> bool:
    """Whether a file a build would collect is one the app never uses: a
    plugin in UNUSED_QT_PLUGINS, the Qt library (and its PySide6 binding)
    that only those plugins need, or one of UNUSED_FILES. The spec leaves
    these out."""
    parts = Path(destination).parts
    name = parts[-1] if parts else ""
    if name.lower() in UNUSED_FILES:
        return True
    if len(parts) >= 2 and parts[-2] in ("imageformats", "iconengines"):
        return qt_plugin_name(name) in UNUSED_QT_PLUGINS
    for library in UNUSED_QT_LIBRARIES:
        if name in (f"Qt{library}", f"Qt6{library}.dll") or f"Qt{library}.framework" in parts:
            return True
        if name.split(".")[0] == f"Qt{library}" and name.endswith((".so", ".pyd")):
            return True
    return False


# -- third-party code inside Qt and Python -------------------------------------------------

@dataclass(frozen=True)
class Rule:
    """Where one piece of third-party code is: in which Qt library or plugin,
    recognised by any of `markers` (none: always part of it), on which
    platforms only (none: all), and on which it is assumed even without a
    marker (where Qt's own builds always include it)."""

    inside: Inside
    library: str
    markers: Tuple[bytes, ...] = ()
    platforms: Tuple[str, ...] = ()
    version: Optional[Callable[[bytes], str]] = None
    assumed_on: Tuple[str, ...] = ()

    def find(self, build: Build) -> Optional[Inside]:
        if self.platforms and build.platform not in self.platforms:
            return None
        data = build.qt(self.library)
        if data is None:
            return None
        marked = not self.markers or any(marker in data for marker in self.markers)
        if not marked and build.platform not in self.assumed_on:
            return None
        return replace(self.inside, version=self.version(data)) if self.version else self.inside


def _search(pattern: bytes) -> Callable[[bytes], str]:
    def version(data: bytes) -> str:
        match = re.search(pattern, data)
        return match.group(1).decode("ascii") if match else ""

    return version


#: hb_version_string(), a few strings before one of HarfBuzz's own.
_HARFBUZZ_VERSION = rb"\x00(\d{1,2}\.\d{1,2}\.\d{1,2})\x00(?:[^\x00]{0,8}\x00){0,4}start table morx"
#: libpng's version text is in png_get_copyright(), which only a Qt GUI that
#: exports libpng's functions keeps (macOS); every build has its version check,
#: which names libpng and compares against the bare version string.
_LIBPNG = (b"libpng version", b"Application built with libpng-")


def _libpng_version(data: bytes) -> str:
    """The version libpng's text gives, or else its bare version string
    ("1.6.58"), when that is the only one of its kind in the library."""
    found = _search(rb"libpng version (1\.\d+\.\d+)")(data)
    if found:
        return found
    bare = {match.decode("ascii") for match in re.findall(rb"(?<=\x00)1\.6\.\d{1,3}(?=\x00)", data)}
    return bare.pop() if len(bare) == 1 else ""


#: The first of double-conversion's cached powers of ten, as it is stored.
_DOUBLE_CONVERSION = bytes.fromhex("88021c08a0d58ffa")
_PUBLIC = "Dedicated to the public domain under CC0-1.0, which sets no conditions"

QT_RULES: List[Rule] = [
    # Qt Core
    Rule(Inside("Apache Tika MIME type definitions", "Qt Core", "Apache-2.0",
                "Copyright 2026 The Apache Software Foundation", "Apache-2.0"),
         "Core", (b"freedesktop.org.xml",)),
    Rule(Inside("BLAKE2 reference implementation", "Qt Core", "CC0-1.0", "Copyright 2012, Samuel Neves",
                note="Offered under CC0-1.0, the OpenSSL licence or Apache-2.0, and used under CC0-1.0, which "
                "sets no conditions"), "Core", (b"Blake2b_",)),
    # Qt's own zlib: Windows has no system zlib, so Qt's builds for it always
    # include theirs; elsewhere they use the system's.
    Rule(Inside("zlib", "Qt Core", "Zlib", "(C) 1995-2026 Jean-loup Gailly and Mark Adler", "zlib"),
         "Core", (b" deflate 1.", b" inflate 1."), version=_search(rb" (?:de|in)flate (1\.\d+\.\d+)"),
         assumed_on=("win32",)),
    Rule(Inside("Easing equations by Robert Penner", "Qt Core", "BSD-3-Clause",
                "Copyright (c) 2001 Robert Penner", "easing"), "Core"),
    Rule(Inside("double-conversion", "Qt Core", "BSD-3-Clause", "Copyright 2006-2011, the V8 project authors",
                "double-conversion"), "Core", (_DOUBLE_CONVERSION,)),
    Rule(Inside("MD4, MD5 and SHA-1", "Qt Core", "public domain", "MD4 written by Alexander Peslyak (Solar "
                "Designer) in 2001; MD5 written by Colin Plumb in 1993; SHA-1 by Dominik Reichl",
                note="Placed in the public domain"), "Core"),
    Rule(Inside("PCRE2", "Qt Core", "BSD-3-Clause WITH PCRE2-exception",
                "Copyright (c) 1997-2007 University of Cambridge; Copyright (c) 2007-2024 Philip Hazel; "
                "Copyright (c) 2010-2024 Zoltan Herczeg", "pcre2"),
         "Core", (b"PCRE2",), version=_search(rb"(10\.\d+) \d{4}-\d{2}-\d{2}")),
    Rule(Inside("PCRE2 just-in-time compiler (SLJIT)", "Qt Core", "BSD-2-Clause", "Copyright Zoltan Herczeg",
                "sljit"), "Core", (b"PCRE2",)),
    Rule(Inside("QEventDispatcher on macOS", "Qt Core", "BSD-3-Clause", "Copyright (c) 2007-2008, Apple, Inc.",
                "qeventdispatcher-cf"), "Core", (b"QEventDispatcherCoreFoundation",)),
    Rule(Inside("SHA-3 (Keccak)", "Qt Core", "CC0-1.0", "The Keccak team", note=_PUBLIC), "Core", (b"Keccak_",)),
    Rule(Inside("SHA-3 brg_endian", "Qt Core", "BSD-2-Clause",
                "Copyright (c) 1998-2013, Brian Gladman, Worcester, UK. All rights reserved.", "brg-endian"),
         "Core", (b"Keccak_",)),
    Rule(Inside("SHA-384 and SHA-512 (RFC 6234)", "Qt Core", "BSD-3-Clause",
                "Copyright (c) 2011 IETF Trust and the persons identified as authors of the code", "rfc6234"),
         "Core", (b"Sha384",)),
    Rule(Inside("SipHash", "Qt Core", "CC0-1.0", "Copyright (C) 2012-2014 Jean-Philippe Aumasson; Copyright (C) "
                "2012-2014 Daniel J. Bernstein; Copyright (C) 2016 Intel Corporation", note=_PUBLIC), "Core"),
    Rule(Inside("TinyCBOR", "Qt Core", "MIT", "Copyright (C) 2015-2025 Intel Corporation", "tinycbor"),
         "Core", (b"QCborStreamReader",)),
    Rule(Inside("Unicode Character Database", "Qt Core", "Unicode-3.0", "Copyright (C) 1991-2025 Unicode, Inc.",
                "Unicode-3.0"), "Core"),
    Rule(Inside("Unicode Common Locale Data Repository (CLDR)", "Qt Core", "Unicode-3.0",
                "Copyright (C) 2004-2025 Unicode, Inc.", "Unicode-3.0"), "Core"),
    Rule(Inside("forkfd", "Qt Core", "MIT", "Copyright (C) 2016 Intel Corporation; Copyright (C) 2015 "
                "Klarälvdalens Datakonsult AB", "forkfd"), "Core", platforms=("darwin", "linux")),
    Rule(Inside("tl::expected", "Qt Core", "CC0-1.0", "Sy Brand", note=_PUBLIC), "Core"),
    # Qt GUI
    Rule(Inside("Adobe Glyph List for New Fonts", "Qt GUI", "BSD-3-Clause",
                "Copyright 2002, 2003, 2005, 2006, 2008, 2010, 2015 Adobe Systems", "aglfn"), "Gui"),
    Rule(Inside("FreeType", "Qt GUI", "FTL", "Copyright (C) 1996-2026 David Turner, Robert Wilhelm, Werner "
                "Lemberg and the other FreeType authors", "FTL",
                "Offered under the FreeType License or GPL-2.0, and used under the FreeType License. This "
                "software is based in part on the work of the FreeType Team (https://www.freetype.org)"),
         "Gui", (b"FT_New_Face",)),
    Rule(Inside("FreeType anti-aliasing rasterizer", "Qt GUI", "FTL",
                "Copyright 2000-2016 by David Turner, Robert Wilhelm, and Werner Lemberg", "FTL",
                "Used under the FreeType License"), "Gui"),
    Rule(Inside("FreeType BDF driver", "Qt GUI", "MIT", "Copyright (C) 2001-2002 Francesco Zappa Nardelli; "
                "Copyright 2000 Computing Research Labs, New Mexico State University", "freetype-bdf"),
         "Gui", (b"\x00bdf\x00",)),
    Rule(Inside("FreeType PCF driver", "Qt GUI", "MIT AND MIT-open-group", "Copyright (C) 2000 Francesco Zappa "
                "Nardelli; Copyright 1990, 1994, 1998 The Open Group", "freetype-pcf"), "Gui", (b"\x00pcf\x00",)),
    Rule(Inside("Emoji Segmenter", "Qt GUI", "Apache-2.0", "Copyright 2019 Google LLC", "Apache-2.0"),
         "Gui", (b"qt.text.emojisegmenter",)),
    Rule(Inside("HarfBuzz", "Qt GUI", "MIT", "Copyright © 2010-2022 Google, Inc. and the other HarfBuzz authors "
                "listed in its licence", "harfbuzz"),
         "Gui", (b"start table morx",), version=_search(_HARFBUZZ_VERSION)),
    Rule(Inside("libpng", "Qt GUI", "libpng-2.0", "Copyright (c) 1995-2026 The PNG Reference Library Authors; "
                "Copyright (c) 2018-2026 Cosmin Truta", "libpng"),
         "Gui", _LIBPNG, version=_libpng_version),
    Rule(Inside("MD4C", "Qt GUI", "MIT", "Copyright © 2016-2024 Martin Mitáš", "md4c"),
         "Gui", (b"QTextMarkdownImporter",)),
    Rule(Inside("Smooth scaling algorithm", "Qt GUI", "BSD-2-Clause AND Imlib2", "Copyright (C) 2004, 2005 "
                "Daniel M. Duley; (C) Carsten Haitzler and various contributors", "smooth-scaling",
                "Based on Imlib2's smooth scaling, which this software uses"), "Gui"),
    Rule(Inside("WebGradients", "Qt GUI", "MIT", "Copyright (c) 2017 itmeo", "webgradients"), "Gui"),
    Rule(Inside("sRGB colour profile", "Qt GUI", "ICC", "Copyright International Color Consortium, 2015",
                "icc-srgb"), "Gui", (b"sRGB2014",)),
    Rule(Inside("OpenGL headers", "Qt GUI", "MIT", "Copyright (c) 2013-2014 The Khronos Group Inc.",
                "opengl-headers"), "Gui", platforms=("win32", "linux")),
    Rule(Inside("D3D12 Memory Allocator", "Qt GUI", "MIT", "Copyright (c) 2019-2022 Advanced Micro Devices, Inc.",
                "d3d12-memory-allocator"), "Gui", platforms=("win32",)),
    Rule(Inside("Mipmap generator for D3D12", "Qt GUI", "MIT", "Copyright (c) 2015 Microsoft", "d3d12-mipmap"),
         "Gui", platforms=("win32",)),
    Rule(Inside("Vulkan Memory Allocator", "Qt GUI", "MIT", "Copyright (c) 2017-2025 Advanced Micro Devices, Inc.",
                "vulkan-memory-allocator"), "Gui", (b"vkGetInstanceProcAddr",)),
    Rule(Inside("Vulkan API Registry", "Qt GUI", "MIT", "Copyright (c) 2015-2025 The Khronos Group Inc.",
                "vulkan-registry", "Offered under Apache-2.0 or MIT, and used under MIT"),
         "Gui", (b"vkGetInstanceProcAddr",)),
    # Qt platform plugins
    Rule(Inside("Cocoa platform plugin", "Qt's macOS platform plugin", "BSD-3-Clause",
                "Copyright (c) 2007-2008, Apple, Inc.", "cocoa-platform-plugin"), "qcocoa"),
    Rule(Inside("Wintab API", "Qt's Windows platform plugin", "LicenseRef-LCS-Telegraphics",
                "Copyright 1991-1998 by LCS/Telegraphics", note="It may be freely used, copied, or distributed "
                "without compensation or licensing restrictions"), "qwindows"),
    # Qt Network
    Rule(Inside("The Public Suffix List", "Qt Network", "MPL-2.0", "The Public Suffix List is an initiative of "
                "Mozilla, maintained as a community resource", "MPL-2.0", "Its source form is the list itself, "
                "at https://publicsuffix.org/list/ and in the Qt source"), "Network", (b"qIsEffectiveTLD",)),
    Rule(Inside("libpsl's Public Suffix List lookup", "Qt Network", "BSD-3-Clause",
                "Copyright 2014-2016 The Chromium Authors. All rights reserved.", "libpsl"),
         "Network", (b"qIsEffectiveTLD",)),
    # Qt D-Bus
    Rule(Inside("libdbus-1 headers", "Qt D-Bus", "AFL-2.1", "Copyright (C) 2002, 2003 CodeFactory AB; Copyright "
                "(C) 2004, 2005 Red Hat, Inc.", "AFL-2.1", "Offered under AFL-2.1 or GPL-2.0-or-later, and used "
                "under AFL-2.1"), "DBus"),
]

#: Code no rule above covers, by a string it leaves: a build that ships it
#: fails, rather than shipping it unattributed. libjpeg, libtiff and libwebp
#: are in the image-format plugins every build leaves out, and Mesa is
#: PySide6's opengl32sw.dll (see UNUSED_FILES): llvmpipe names itself with
#: this string as its OpenGL renderer.
UNATTRIBUTED = {
    b"libjpeg-turbo version": "libjpeg-turbo",
    b"not supported by libtiff": "libtiff",
    b"QWebpHandler": "libwebp",
    b"pixman_": "Pixman",
    b"llvmpipe (LLVM ": "Mesa (software OpenGL)",
}


def _freetype_version(build: Build) -> str:
    """The shipped Qt GUI's own answer (FreeType's FT_Library_Version), where
    it exports FreeType (macOS); empty elsewhere."""
    path = build.files.get("QtGui")
    if path is None:
        return ""
    probe = (
        "import ctypes, sys\n"
        "lib = ctypes.CDLL(sys.argv[1]); handle = ctypes.c_void_p()\n"
        "assert lib.FT_Init_FreeType(ctypes.byref(handle)) == 0\n"
        "v = [ctypes.c_int() for _ in range(3)]\n"
        "lib.FT_Library_Version(handle, *map(ctypes.byref, v)); print('.'.join(str(x.value) for x in v))\n"
    )
    try:
        result = subprocess.run([sys.executable, "-I", "-c", probe, str(path)], capture_output=True, text=True,
                                timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    version = result.stdout.strip()
    return version if re.fullmatch(r"\d+\.\d+\.\d+", version) else ""


def inside_qt(build: Build) -> List[Inside]:
    for marker, name in UNATTRIBUTED.items():
        files = build.search(marker)
        if files:
            raise SystemExit(f"This build ships {name} (in {', '.join(files)}), which tools/make_notices.py "
                             "has no notice for")
    found = [inside for inside in (rule.find(build) for rule in QT_RULES) if inside is not None]
    return [replace(item, version=_freetype_version(build)) if item.name == "FreeType" else item for item in found]


#: Python modules the notices depend on.
PYTHON_MODULES = (
    "_random", "select", "encodings.uu_codec", "_sha2", "_md5", "_sha1", "_sha3", "_blake2", "_hmac", "_zstd",
    "pyexpat", "zlib", "_ctypes", "_bz2", "_decimal", "_lzma", "_hashlib", "_ssl",
)
#: Modules with a notice of their own in Python's licence that no build
#: ships yet; one that starts shipping needs its notice added here first.
PYTHON_NOTICES_MISSING = ("http.cookies", "trace", "xmlrpc.client", "asyncio")


def inside_python(build: Build) -> List[Inside]:
    """The notices in Python's licence ("Licenses and Acknowledgements for
    Incorporated Software") for what this build ships, and HACL*'s."""
    missing = sorted(name for name in PYTHON_NOTICES_MISSING if name in build.modules)
    if missing:
        raise SystemExit(f"Add the notice Python's licence has for {', '.join(missing)} to tools/make_notices.py")
    found = [
        Inside("SipHash24", "Python", "MIT", "Copyright (c) 2013 Marek Majkowski", "python-siphash"),
        Inside("dtoa and strtod", "Python", "dtoa", "Copyright (c) 1991, 2000, 2001 by Lucent Technologies",
               "python-dtoa"),
        Inside("cfuhash (tracemalloc's hash table)", "Python", "BSD-3-Clause", "Copyright (c) 2005 Don Owens",
               "python-cfuhash"),
        Inside("Unicode Character Database", "Python", "Unicode-3.0", "Copyright (C) 1991-2025 Unicode, Inc.",
               "Unicode-3.0"),
    ]
    if sys.version_info >= (3, 13):
        found += [
            Inside("mimalloc", "Python", "MIT", "Copyright (c) 2018-2021 Microsoft Corporation, Daan Leijen",
                   "python-mimalloc"),
            Inside("Global Unbounded Sequences (qsbr.c)", "Python", "BSD-2-Clause",
                   "Copyright (c) 2019,2020 Jeffrey Roberson", "python-gus"),
        ]
    if build.extension("_random") is not None:
        found.append(Inside("Mersenne Twister (random)", "Python", "BSD-3-Clause",
                            "Copyright (C) 1997 - 2002, Makoto Matsumoto and Takuji Nishimura",
                            "python-mersenne-twister"))
    select = build.extension("select")
    if select is not None and b"kqueue" in select:
        found.append(Inside("select.kqueue", "Python", "BSD-2-Clause",
                            "Copyright (c) 2000 Doug White, 2006 James Knight, 2007 Christian Heimes",
                            "python-kqueue"))
    if "encodings.uu_codec" in build.modules:
        found.append(Inside("uu codec", "Python", "uu", "Copyright (c) 1994 by Lance Ellinghouse", "python-uu"))
    if any(build.extension(name) is not None for name in ("_sha2", "_md5", "_sha1", "_sha3", "_blake2", "_hmac")):
        found.append(Inside("HACL* (hashlib's MD5, SHA-1, SHA-2, SHA-3, BLAKE2 and HMAC)", "Python", "MIT",
                            "Copyright (c) 2016-2022 INRIA, CMU and Microsoft Corporation; Copyright (c) "
                            "2022-2023 HACL* Contributors", "python-hacl"))
    if build.extension("_zstd") is not None:
        found.append(Inside("Zstandard bindings (from pyzstd)", "Python", "BSD-3-Clause",
                            "Copyright (c) 2020-present, Ma Lin and contributors", "python-zstd-bindings"))
    return found


#: OPENSSL_VERSION_TEXT, which every libcrypto carries: "OpenSSL 3.0.13 30 Jan 2024".
_OPENSSL_VERSION = re.compile(rb"OpenSSL (\d+\.\d+\.\d+[a-z]?) +\d{1,2} [A-Z][a-z]{2} \d{4}\x00")


@dataclass(frozen=True)
class OpenSSLCopy:
    """One copy of OpenSSL a build ships: its libcrypto (and libssl, if it
    ships one beside it), the version the libcrypto file says it is, and
    whether it is the one Qt Network's OpenSSL backend loads rather than the
    one Python's ssl and hashlib modules link to."""

    crypto: str
    ssl: str
    version: str
    for_qt: bool


def openssl_copies(build: Build) -> List[OpenSSLCopy]:
    """Each copy of OpenSSL's libraries among the build's files, and whose it
    is: Python's ssl and hashlib modules link to theirs by file name, and Qt's
    OpenSSL backend names the files it loads. A Windows build ships both, and
    they may be different versions. A copy neither names fails the build."""
    python = b"\x00".join(build.extension(module) or b"" for module in ("_ssl", "_hashlib")).lower()
    qt = b"\x00".join(build.qt(library) or b"" for library in ("qopensslbackend", "Network"))
    qt_names = {stem.decode("ascii").lower() for stem in _QT_OPENSSL_NAME.findall(qt)}
    copies = []
    for name in sorted(build.files, key=str.lower):
        match = _OPENSSL_FILE.match(name)
        if not match or match.group(1).lower() != "crypto":
            continue
        partner = next((other for other in build.files if other.lower() == "libssl" + name[len("libcrypto"):].lower()),
                       "")
        found = _OPENSSL_VERSION.search(build.data(name) or b"")
        version = found.group(1).decode("ascii") if found else ""
        if name.lower().encode() in python:
            copies.append(OpenSSLCopy(name, partner, version, for_qt=False))
        elif name.lower().removesuffix(".dll") in qt_names:
            copies.append(OpenSSLCopy(name, partner, version, for_qt=True))
        else:
            raise SystemExit(f"This build ships {name}, which neither Python's ssl modules nor Qt's OpenSSL backend "
                             "load; tools/make_notices.py can't say whose copy it is")
    return copies


def _openssl(name: str, version: str) -> Component:
    if not version.startswith("3."):
        raise SystemExit(f"{name} is OpenSSL {version or '(version unknown)'}; tools/make_notices.py has the "
                         "licence of OpenSSL 3 (Apache-2.0) only")
    return Component(
        name, version, "Apache-2.0",
        "Copyright (c) The OpenSSL Project Authors; Copyright (c) 1995-1998 Eric A. Young, Tim J. Hudson. "
        "All rights reserved.",
        f"https://github.com/openssl/openssl/releases/download/openssl-{version}/openssl-{version}.tar.gz",
        "Apache-2.0",
    )


def qt_libraries(build: Build) -> List[Component]:
    """Libraries Qt loads that the build ships beside it: the OpenSSL copy Qt
    Network's OpenSSL backend loads (on Windows, where PyInstaller collects
    it)."""
    found = []
    for copy in openssl_copies(build):
        if copy.for_qt:
            if not copy.version:
                raise SystemExit(f"Couldn't read which OpenSSL {copy.crypto} is")
            files = ", ".join(name for name in (copy.crypto, copy.ssl) if name)
            found.append(_openssl(f"OpenSSL for Qt Network ({files})", copy.version))
    return found


def python_libraries(build: Build) -> List[Component]:
    """Libraries Python's own modules use, where the build carries its own copy."""
    found = []
    try:
        import ssl

        running = ssl.OPENSSL_VERSION.split()[1] if ssl.OPENSSL_VERSION.startswith("OpenSSL ") else ""
    except (ImportError, IndexError):
        running = ""
    # Python's copy as its file says (where the build lists its files), or
    # as the Python making the build reports it.
    own = [copy for copy in openssl_copies(build) if not copy.for_qt]
    for copy in own:
        found.append(_openssl("OpenSSL (libcrypto, libssl)", copy.version or running))
    if not own and running and build.bundles("_hashlib", "libcrypto"):
        found.append(_openssl("OpenSSL (libcrypto, libssl)", running))
    try:
        from compression import zstd  # Python 3.14+

        if build.bundles("_zstd", "libzstd"):
            version = ".".join(str(part) for part in zstd.zstd_version_info)
            found.append(Component(
                "Zstandard (libzstd)", version, "BSD-3-Clause",
                "Copyright (c) Meta Platforms, Inc. and affiliates. All rights reserved.",
                f"https://github.com/facebook/zstd/archive/refs/tags/v{version}.tar.gz", "zstd",
            ))
    except ImportError:
        pass
    try:
        import decimal

        if build.bundles("_decimal", "libmpdec"):
            version = decimal.__libmpdec_version__
            found.append(Component(
                "mpdecimal (libmpdec)", version, "BSD-2-Clause", "Copyright (c) 2008-2025 Stefan Krah. All rights reserved.",
                f"https://www.bytereef.org/software/mpdecimal/releases/mpdecimal-{version}.tar.gz", "mpdecimal",
            ))
    except (ImportError, AttributeError):
        pass
    if build.bundles("_lzma", "liblzma"):
        found.append(Component(
            "XZ Utils (liblzma)", "", "0BSD", "The XZ Utils authors (Lasse Collin and others)",
            "https://github.com/tukaani-project/xz", "xz-0BSD",
        ))
    try:
        import pyexpat

        if build.bundles("pyexpat", "libexpat"):
            version = pyexpat.EXPAT_VERSION.removeprefix("expat_")
            found.append(Component(
                "Expat", version, "MIT",
                "Copyright (c) 1998-2000 Thai Open Source Software Center Ltd and Clark Cooper; "
                "Copyright (c) 2001-2025 Expat maintainers",
                f"https://github.com/libexpat/libexpat/releases/tag/R_{version.replace('.', '_')}", "expat",
            ))
    except ImportError:
        pass
    try:
        import zlib

        if build.bundles("zlib", "libz."):
            if getattr(zlib, "ZLIBNG_VERSION", None):
                version = zlib.ZLIBNG_VERSION
                found.append(Component(
                    "zlib-ng", version, "Zlib", "(C) 1995-2024 Jean-loup Gailly and Mark Adler; zlib-ng contributors",
                    f"https://github.com/zlib-ng/zlib-ng/releases/tag/{version}", "zlib",
                ))
            else:
                version = zlib.ZLIB_RUNTIME_VERSION
                found.append(Component(
                    "zlib", version, "Zlib", "(C) 1995-2024 Jean-loup Gailly and Mark Adler",
                    f"https://zlib.net/fossils/zlib-{version}.tar.gz", "zlib",
                ))
    except ImportError:
        pass
    if build.bundles("_bz2", "libbz2"):
        found.append(Component(
            "bzip2 (libbzip2)", "", "bzip2-1.0.6", "Copyright (C) 1996-2019 Julian R Seward. All rights reserved.",
            "https://sourceware.org/bzip2/", "bzip2",
        ))
    if build.bundles("_ctypes", "libffi"):
        found.append(Component(
            "libffi", "", "MIT", "Copyright (c) 1996-2026 Anthony Green, Red Hat, Inc and others.",
            "https://github.com/libffi/libffi", "libffi",
        ))
    return found


# -- the file ---------------------------------------------------------------------------

def _table(components: List[Component]) -> List[str]:
    lines = ["| Component | Version | Licence | Source |", "|---|---|---|---|"]
    for item in components:
        lines.append(f"| {item.name} | {item.version or '-'} | {item.licence} | <{item.source}> |")
    return lines


def _copyrights(components: List[Component]) -> List[str]:
    return [f"- {item.name}: {_sentence(item.copyright)}" for item in components]


def _named(item) -> str:
    return f"{item.name} {item.version}" if item.version else item.name


def _sentence(text: str) -> str:
    return text if text.endswith(".") else text + "."


def render(build: Optional[Build] = None) -> str:
    """The whole file, for `build` (by default, what this environment would ship)."""
    build = build or Build.from_environment()
    version = app_version()
    repository = repository_url()
    qt = qt_version()
    series = ".".join(qt.split(".")[:2])
    pyside = installed("PySide6-Essentials") or installed("PySide6")
    shiboken = installed("shiboken6")
    if not pyside or not shiboken:
        raise SystemExit("PySide6 and shiboken6 must be installed: the notices describe what the build bundles.")
    python = platform.python_version()
    python_series = ".".join(python.split(".")[:2])
    release = re.match(r"\d+\.\d+\.\d+", python).group(0)
    pyinstaller = installed("pyinstaller")
    pyobjc = {name: installed(name) for name in ("pyobjc-core", "pyobjc-framework-Cocoa", "pyobjc-framework-Quartz")}
    pyobjc = {name: number for name, number in pyobjc.items() if number}
    used = []  # licence texts, in order of first use

    def use(key: str) -> str:
        if key not in used:
            used.append(key)
        return f"[{TEXTS[key]}](#{_anchor(TEXTS[key])})"

    qt_parts = [
        Component("Qt", qt, "LGPL-3.0-only", "Copyright (C) The Qt Company Ltd. and other contributors.",
                  f"https://download.qt.io/archive/qt/{series}/{qt}/single/qt-everywhere-src-{qt}.tar.xz",
                  "LGPL-3.0"),
        Component("Qt for Python: PySide6", pyside, "LGPL-3.0-only", "Copyright (C) The Qt Company Ltd.",
                  f"https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-{pyside}-src/"
                  f"pyside-setup-everywhere-src-{pyside}.tar.xz", "LGPL-3.0"),
        Component("Qt for Python: Shiboken6", shiboken, "LGPL-3.0-only", "Copyright (C) The Qt Company Ltd.",
                  f"https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-{shiboken}-src/"
                  f"pyside-setup-everywhere-src-{shiboken}.tar.xz", "LGPL-3.0"),
    ]
    python_part = Component(
        "Python", python, "PSF-2.0",
        "Copyright (c) 2001 Python Software Foundation. All rights reserved. "
        "Copyright (c) 2000 BeOpen.com; (c) 1995-2001 CNRI; (c) 1991-1995 Stichting Mathematisch Centrum.",
        f"https://www.python.org/ftp/python/{release}/Python-{python}.tar.xz", "PSF-2.0",
    )
    libraries = python_libraries(build)
    qt_libs = qt_libraries(build)
    in_qt = inside_qt(build)
    in_python = inside_python(build)
    pyobjc_parts = [
        Component(name, number, "MIT", "Copyright 2002, 2003 - Bill Bumgarner, Ronald Oussoren, Steve Majewski, "
                  "Lele Gaifax, et.al.; Copyright 2003-2025 - Ronald Oussoren",
                  f"https://github.com/ronaldoussoren/pyobjc/tree/v{number}", "MIT-PyObjC")
        for name, number in pyobjc.items()
    ]
    tools = []
    if pyinstaller:
        tools.append(Component(
            "PyInstaller bootloader and run-time hooks", pyinstaller, "GPL-2.0-or-later WITH Bootloader-exception; "
            "Apache-2.0", "Copyright (c) 2010-2023, PyInstaller Development Team; Copyright (c) 2005-2009, "
            "Giovanni Bajo; based on previous work under copyright (c) 2002 McMillan Enterprises, Inc.",
            f"https://github.com/pyinstaller/pyinstaller/tree/v{pyinstaller}", "Apache-2.0",
        ))
    everything = qt_parts + qt_libs + [python_part] + libraries + pyobjc_parts + tools

    def inside_lines(items: List[Inside]) -> List[str]:
        lines = []
        for item in items:
            terms = f"{item.licence}, {use(item.text)}" if item.text else item.licence
            note = f" {_sentence(item.note)}" if item.note else ""
            lines.append(f"- {_named(item)}, in {item.within}: {terms}. {_sentence(item.copyright)}{note}")
        return lines

    out = [
        "# Third-party notices",
        "",
        f"DoubleClick Fixer {version} is copyright (c) 2026 Arnav Goel and is released under the MIT License "
        f"(the LICENSE file, and its source at <{repository}>). It is built on the software below, each under its "
        "own licence. This file lists the exact versions in this build, where to get their source, and the full "
        "licence texts.",
        "",
        f"_Generated by tools/make_notices.py from the files this copy ships, built with Python {python} on "
        f"{platform.system()} {platform.machine()}._",
        "",
        "## Summary",
        "",
        *_table(everything),
        "",
        "## Qt and Qt for Python (LGPL-3.0)",
        "",
        f"DoubleClick Fixer uses Qt {qt} and Qt for Python (PySide6 {pyside} and Shiboken6 {shiboken}) under the "
        f"{use('LGPL-3.0')}. The LGPL is a set of additional permissions on top of the {use('GPL-3.0')}, so both "
        "texts are included below. Qt and Qt for Python are also available under the GPL-2.0, the GPL-3.0 and "
        "commercial licences from The Qt Company.",
        "",
        *_copyrights(qt_parts),
        "",
        "They are unmodified, and the app loads them as separate libraries, which you may replace with your own "
        "build of the same or a compatible version:",
        "",
        "- macOS: in `DoubleClick Fixer.app/Contents/Frameworks`, the Qt frameworks are in `PySide6/Qt/lib`, "
        "and `libpyside6` and `libshiboken6` in `PySide6` and `shiboken6`. After replacing one, sign the app "
        "again (for example `codesign --force --deep --sign - \"DoubleClick Fixer.app\"`); macOS then treats "
        "it as a different app, so allow it again in Privacy & Security.",
        "- Windows, installed with DoubleClickFixer-Setup.exe: they are the `Qt6*.dll`, `pyside6*.dll` and "
        "`shiboken6*.dll` files in the `_internal` folder of the program folder. Replace them there.",
        "- Windows, the portable DoubleClickFixer.exe: it is a single file that unpacks these libraries to a "
        "temporary folder each time it starts, so they can't be replaced in place. Instead, rebuild it with "
        "your own Qt or Qt for Python from the app's source and its build recipe (`doubleclick-fixer.spec`, "
        "built by `installer/build_windows.ps1`), or use the installed copy.",
        "",
        f"The app's source code and build recipe are at <{repository}>. Its licence places no restriction on "
        "modifying these libraries or on reverse engineering to debug such modifications.",
        "",
        "The complete corresponding source code for exactly these versions is published by The Qt Company, "
        "whose download server keeps every release:",
        "",
        *[f"- {item.name} {item.version}: <{item.source}>" for item in qt_parts],
        "",
    ]
    if in_qt:
        out += [
            "### Third-party code inside Qt",
            "",
            "Qt's libraries and plugins in this build contain code from others, each under its own licence. Its "
            "source is part of the Qt source above.",
            "",
            *inside_lines(in_qt),
            "",
        ]
    if qt_libs:
        out += [
            "### OpenSSL for Qt Network",
            "",
            "Qt Network's OpenSSL backend loads OpenSSL from files of its own, and this build ships them: a "
            "separate copy from Python's (below), which can be a different version.",
            "",
            *[f"- {_named(item)}: {item.licence}, {use(item.text)}. {_sentence(item.copyright)} "
              f"Source: <{item.source}>" for item in qt_libs],
            "",
        ]
    out += [
        "## Python",
        "",
        f"The app runs on its own copy of Python {python}, under {use('PSF-2.0')}.",
        "",
        *_copyrights([python_part]),
        "",
        f"Source: <{python_part.source}>. Python's licence and its list of incorporated software are also at "
        f"<https://docs.python.org/{python_series}/license.html>.",
        "",
    ]
    if in_python:
        out += [
            "### Third-party code inside Python",
            "",
            "This copy of Python contains code from others, each under its own licence. Its source is part of the "
            "Python source above.",
            "",
            *inside_lines(in_python),
            "",
        ]
    if libraries:
        out += [
            "### Libraries that come with Python",
            "",
            "Python's own modules use these libraries, and this build carries its own copy of each, as a separate "
            "file or compiled into the module. Libraries the operating system provides are not part of the app "
            "and are not listed.",
            "",
            *[f"- {_named(item)}: {item.licence}, {use(item.text)}. {_sentence(item.copyright)} "
              f"Source: <{item.source}>" for item in libraries],
            "",
        ]
    if pyobjc_parts:
        out += [
            "## PyObjC (macOS)",
            "",
            f"The macOS app talks to the system through PyObjC, under {use('MIT-PyObjC')}.",
            "",
            *[f"- {item.name} {item.version}: <{item.source}>" for item in pyobjc_parts],
            "",
            _sentence(pyobjc_parts[0].copyright),
            "",
        ]
    if tools:
        item = tools[0]
        out += [
            "## PyInstaller",
            "",
            f"The app is packaged with PyInstaller {item.version}. Its bootloader, which starts the app, is under "
            "the GPL-2.0-or-later with the PyInstaller bootloader exception, which gives unlimited permission to "
            "distribute it as part of another program without the GPL's conditions applying to that program. "
            f"The run-time hooks and modules it adds are under the {use('Apache-2.0')}.",
            "",
            _sentence(item.copyright),
            "",
            f"Source: <{item.source}>",
            "",
        ]
    out += [
        "## Windows installer",
        "",
        "The Windows installer (DoubleClickFixer-Setup.exe) is made with Inno Setup, copyright (C) Jordan "
        "Russell and Martijn Laan, <https://jrsoftware.org/isinfo.php>, under the Inno Setup License, and keeps "
        "Inno Setup's own copyright notice. Nothing from Inno Setup is part of the app itself.",
        "",
        "## Licence texts",
        "",
    ]
    from_environment = {
        "PSF-2.0": python_licence,
        "MIT-PyObjC": lambda: licence_file("pyobjc-framework-Cocoa", "LICENSE.txt"),
    }
    for key in used:
        if key in from_environment:
            text = from_environment[key]() or f"See <https://docs.python.org/{python_series}/license.html>."
        else:
            text = (LICENSES / f"{key}.txt").read_text(encoding="utf-8")
        out += [f"### {TEXTS[key]}", "", "```text", text.rstrip("\n"), "```", ""]
    return "\n".join(out)


def _anchor(heading: str) -> str:
    """GitHub's anchor for a Markdown heading."""
    slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
    return slug


def write(output: Path, build: Optional[Build] = None) -> str:
    """Write the file for `build` to `output`; returns what it wrote."""
    text = render(build)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8", newline="\n")
    return text


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="where to write (default: %(default)s)")
    parser.add_argument("--check", action="store_true", help="only report whether --output matches")
    parser.add_argument("--bundle", type=Path, help="describe this finished build (an .app or a program folder)")
    arguments = parser.parse_args(argv)
    build = Build.from_bundle(arguments.bundle) if arguments.bundle else None
    if arguments.check:
        current = arguments.output.read_text(encoding="utf-8") if arguments.output.exists() else ""
        if current != render(build):
            what = f"what {arguments.bundle} ships" if arguments.bundle else "this environment"
            print(f"{arguments.output} doesn't match {what}; run tools/make_notices.py", file=sys.stderr)
            return 1
        return 0
    text = write(arguments.output, build)
    print(f"Wrote {arguments.output} ({len(text.encode('utf-8')):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
