#!/usr/bin/env python3
"""Write THIRD_PARTY_NOTICES.md: the software DoubleClick Fixer ships that
others wrote, with exact versions, licences, copyright lines, where to get
the source, and the licence texts.

Versions come from the Python environment that runs this script, so run it
with the one that builds the app. The PyInstaller spec does exactly that at
build time and ships the result inside the app (Contents/Resources on macOS,
next to the program files on Windows). The copy at the top of the repository
is the same thing generated on a development Mac; regenerate it with
``python tools/make_notices.py`` when a dependency changes (the tests fail
until it matches requirements.txt).
"""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import os
import platform
import re
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
LICENSES = Path(__file__).resolve().parent / "licenses"
DEFAULT_OUTPUT = ROOT / "THIRD_PARTY_NOTICES.md"


@dataclass
class Component:
    name: str
    version: str
    licence: str  # SPDX identifier
    copyright: str
    source: str  # the source of exactly this version, where one exists
    text: str  # key into TEXTS: the licence text that goes with it


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


def python_libraries() -> List[Component]:
    """Libraries Python's own modules use, as far as this Python has them."""
    found = []
    try:
        import ssl

        version = ssl.OPENSSL_VERSION.split()[1]
        if ssl.OPENSSL_VERSION.startswith("OpenSSL"):
            found.append(Component(
                "OpenSSL (libcrypto, libssl)", version, "Apache-2.0",
                "Copyright (c) The OpenSSL Project Authors; Copyright (c) 1995-1998 Eric A. Young, Tim J. Hudson. "
                "All rights reserved.",
                f"https://github.com/openssl/openssl/releases/download/openssl-{version}/openssl-{version}.tar.gz",
                "Apache-2.0",
            ))
    except (ImportError, IndexError):
        pass
    try:
        from compression import zstd  # Python 3.14+

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

        version = decimal.__libmpdec_version__
        found.append(Component(
            "mpdecimal (libmpdec)", version, "BSD-2-Clause", "Copyright (c) 2008-2025 Stefan Krah. All rights reserved.",
            f"https://www.bytereef.org/software/mpdecimal/releases/mpdecimal-{version}.tar.gz", "mpdecimal",
        ))
    except (ImportError, AttributeError):
        pass
    try:
        import lzma  # noqa: F401 - only whether it is there

        found.append(Component(
            "XZ Utils (liblzma)", "", "0BSD", "The XZ Utils authors (Lasse Collin and others)",
            "https://github.com/tukaani-project/xz", "xz-0BSD",
        ))
    except ImportError:
        pass
    try:
        import pyexpat

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

        if getattr(zlib, "ZLIBNG_VERSION", None):
            version = zlib.ZLIBNG_VERSION
            found.append(Component(
                "zlib-ng", version, "Zlib", "(C) 1995-2026 Jean-loup Gailly and Mark Adler; zlib-ng contributors",
                f"https://github.com/zlib-ng/zlib-ng/releases/tag/{version}", "zlib",
            ))
        else:
            version = zlib.ZLIB_RUNTIME_VERSION
            found.append(Component(
                "zlib", version, "Zlib", "(C) 1995-2026 Jean-loup Gailly and Mark Adler",
                f"https://zlib.net/fossils/zlib-{version}.tar.gz", "zlib",
            ))
    except ImportError:
        pass
    if sys.platform == "win32":  # macOS has it in the system
        found.append(Component(
            "libffi", "", "MIT",
            "Copyright (c) 1996-2026 Anthony Green, Red Hat, Inc and others.",
            "https://github.com/libffi/libffi", "libffi",
        ))
    return found


def _table(components: List[Component]) -> List[str]:
    lines = ["| Component | Version | Licence | Source |", "|---|---|---|---|"]
    for item in components:
        lines.append(f"| {item.name} | {item.version or '-'} | {item.licence} | <{item.source}> |")
    return lines


def _copyrights(components: List[Component]) -> List[str]:
    return [f"- {item.name}: {_sentence(item.copyright)}" for item in components]


def _named(item: Component) -> str:
    return f"{item.name} {item.version}" if item.version else item.name


def _sentence(text: str) -> str:
    return text if text.endswith(".") else text + "."


def render() -> str:
    """The whole file, for the environment this runs in."""
    version = app_version()
    repository = repository_url()
    qt = qt_version()
    pyside = installed("PySide6-Essentials") or installed("PySide6")
    shiboken = installed("shiboken6")
    if not pyside or not shiboken:
        raise SystemExit("PySide6 and shiboken6 must be installed: the notices describe what the build bundles.")
    python = platform.python_version()
    series = ".".join(python.split(".")[:2])
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
                  f"https://download.qt.io/official_releases/qt/{'.'.join(qt.split('.')[:2])}/{qt}/single/"
                  f"qt-everywhere-src-{qt}.tar.xz", "LGPL-3.0"),
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
    libraries = python_libraries()
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
    everything = qt_parts + [python_part] + libraries + pyobjc_parts + tools

    out = [
        "# Third-party notices",
        "",
        f"DoubleClick Fixer {version} is copyright (c) 2026 Arnav Goel and is released under the MIT License "
        f"(the LICENSE file, and its source at <{repository}>). It is built on the software below, each under its "
        "own licence. This file lists the exact versions in this build, where to get their source, and the full "
        "licence texts.",
        "",
        f"_Generated by tools/make_notices.py from the environment that built this copy: Python {python} on "
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
        "They are unmodified and dynamically linked: separate libraries inside the app, which you may replace "
        "with your own build of the same or a compatible version.",
        "",
        "- macOS: in `DoubleClick Fixer.app/Contents/Frameworks`, the Qt frameworks are in `PySide6/Qt/lib`, "
        "and `libpyside6` and `libshiboken6` in `PySide6` and `shiboken6`. After replacing one, sign the app "
        "again (for example `codesign --force --deep --sign - \"DoubleClick Fixer.app\"`); macOS then treats "
        "it as a different app, so allow it again in Privacy & Security.",
        "- Windows: they are the `Qt6*.dll`, `pyside6*.dll` and `shiboken6*.dll` files in the program folder "
        "(in its `_internal` folder where there is one). The single-file portable program unpacks the same "
        "files to a temporary folder each time it starts.",
        "",
        f"The app's own source code and its build recipe (`doubleclick-fixer.spec`) are at <{repository}>, so "
        "it can also be rebuilt against a modified Qt or Qt for Python. Its licence places no restriction on "
        "modifying these libraries or on reverse engineering to debug such modifications.",
        "",
        "Complete corresponding source code for exactly these versions:",
        "",
        *[f"- {item.name} {item.version}: <{item.source}>" for item in qt_parts],
        "",
        f"If a link stops working, open an issue at <{repository}/issues> and a copy of that source will be "
        "provided.",
        "",
        f"Qt itself contains third-party code under other licences, listed for this Qt series at "
        f"<https://doc.qt.io/qt-{'.'.join(qt.split('.')[:2])}/licenses-used-in-qt.html>.",
        "",
        "## Python",
        "",
        f"The app runs on its own copy of Python {python}, under {use('PSF-2.0')}.",
        "",
        *_copyrights([python_part]),
        "",
        f"Source: <{python_part.source}>. Python includes software from others under their own licences; see "
        f"<https://docs.python.org/{series}/license.html#licenses-and-acknowledgements-for-incorporated-software>.",
        "",
    ]
    if libraries:
        out += [
            "### Libraries that come with Python",
            "",
            "Python's own modules use these libraries. Whether a build includes each one as a separate file "
            "(on macOS, in `Contents/Frameworks`), compiles it into Python's modules, or uses the operating "
            "system's copy depends on how that Python was built; each is listed if the Python that made this "
            "build has it.",
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
            text = from_environment[key]() or f"See <https://docs.python.org/{series}/license.html>."
        else:
            text = (LICENSES / f"{key}.txt").read_text(encoding="utf-8")
        out += [f"### {TEXTS[key]}", "", "```text", text.rstrip("\n"), "```", ""]
    return "\n".join(out)


def _anchor(heading: str) -> str:
    """GitHub's anchor for a Markdown heading."""
    slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
    return slug


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="where to write (default: %(default)s)")
    parser.add_argument("--check", action="store_true", help="only report whether --output is up to date")
    arguments = parser.parse_args(argv)
    text = render()
    if arguments.check:
        current = arguments.output.read_text(encoding="utf-8") if arguments.output.exists() else ""
        if current != text:
            print(f"{arguments.output} is out of date; run python tools/make_notices.py", file=sys.stderr)
            return 1
        return 0
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(text, encoding="utf-8", newline="\n")
    print(f"Wrote {arguments.output} ({len(text.encode('utf-8')):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
