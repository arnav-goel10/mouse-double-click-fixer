# PyInstaller build recipe for both platforms.
#
# Windows builds one of two shapes: by default the portable DoubleClickFixer.exe,
# one file that unpacks itself at each launch; with DCF_ONEDIR=1 the folder the
# installer ships, which launches (and starts at every sign-in) without
# unpacking anything. installer/build_windows.ps1 builds both.
import os
import sys

import re
from pathlib import Path

# One source of truth for the version: app/__init__.py.
VERSION = re.search(r'__version__ = "([^"]+)"', Path("app/__init__.py").read_text()).group(1)

# Qt ships far more than this app uses; leaving the rest out keeps the
# download small and the startup fast.
EXCLUDES = [
    "tkinter",
    "unittest",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.Qt3DCore",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtOpenGL",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
]

datas = []
ci_update_key = os.environ.get("DCF_CI_UPDATE_KEY", "")
if ci_update_key:
    # CI's end-to-end job only: the public half of a key made for that run, so
    # its stand-in release can be signed (tools/ci_update_key.py). A release
    # must never carry it; release.yml refuses to build with it set.
    if os.environ.get("GITHUB_REF", "").startswith("refs/tags/"):
        raise SystemExit("DCF_CI_UPDATE_KEY is set for a tag build: a release must never trust CI's key")
    if sys.platform == "darwin" or Path(ci_update_key).name != "dcf-ci-update-key.pub":
        raise SystemExit(f"DCF_CI_UPDATE_KEY must name a Windows build's dcf-ci-update-key.pub, not {ci_update_key}")
    datas.append((ci_update_key, "."))

hiddenimports = []
if sys.platform == "darwin":
    # The event tap is reached through PyObjC at runtime.
    from PyInstaller.utils.hooks import collect_submodules

    hiddenimports += (
        collect_submodules("Quartz") + collect_submodules("AppKit") + collect_submodules("Foundation")
    )

# macOS: drop environment variables that would load code from outside the
# app, before any other code runs (see the hook for which and why).
RUNTIME_HOOKS = ["installer/runtime_hooks/scrub_env.py"] if sys.platform == "darwin" else []

analysis = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=RUNTIME_HOOKS,
    excludes=EXCLUDES,
)

import importlib.util

_spec = importlib.util.spec_from_file_location("make_notices", "tools/make_notices.py")
make_notices = importlib.util.module_from_spec(_spec)
sys.modules["make_notices"] = make_notices
_spec.loader.exec_module(make_notices)

# Leave out the Qt image-format and icon-engine plugins the app never uses
# (it draws its icons in code and only round-trips PNG, which Qt GUI has
# built in), and Qt SVG, which only they need. Their code (libjpeg, libtiff,
# libwebp and more) is then neither shipped nor attributed. On Windows also
# Qt's OpenSSL backend with the OpenSSL files PyInstaller found for it on
# this machine's PATH (TLS goes through Windows' Schannel, app/tls.py), and
# the Universal C Runtime, which Windows 10 and later have built in.
left_out = [entry for entry in analysis.binaries + analysis.datas if make_notices.unused_qt_file(entry[0])]
analysis.binaries = [entry for entry in analysis.binaries if not make_notices.unused_qt_file(entry[0])]
analysis.datas = [entry for entry in analysis.datas if not make_notices.unused_qt_file(entry[0])]
for destination, source, *_rest in sorted(left_out):
    size = os.path.getsize(source) if source and os.path.isfile(source) else 0
    print(f"left out: {destination} ({size / 1e6:.1f} MB, from {source})")

# Every build carries the licences of what it bundles (Qt's LGPL among them),
# worked out from the very files it ships: Contents/Resources on macOS,
# beside the program files on Windows (app/notices.py finds it).
NOTICES = Path("build", "notices", "THIRD_PARTY_NOTICES.md")
make_notices.write(NOTICES, make_notices.Build.from_toc(analysis.binaries, analysis.pure, analysis.datas))
analysis.datas.append(("THIRD_PARTY_NOTICES.md", str(NOTICES.resolve()), "DATA"))

pyz = PYZ(analysis.pure)

if sys.platform == "darwin":
    # A real .app is a folder: the executable and its libraries sit inside
    # the bundle, so launching needs no unpacking and code signing covers
    # every file.
    exe = EXE(
        pyz,
        analysis.scripts,
        [],
        exclude_binaries=True,
        name="DoubleClickFixer",
        console=False,
    )
    collected = COLLECT(exe, analysis.binaries, analysis.datas, name="DoubleClickFixer")
    app = BUNDLE(
        collected,
        name="DoubleClick Fixer.app",
        icon="installer/assets/icon.icns",
        bundle_identifier="com.doubleclickfixer.app",
        info_plist={
            "CFBundleName": "DoubleClick Fixer",
            "CFBundleDisplayName": "DoubleClick Fixer",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            # Qt 6.11 needs macOS 13; macOS then refuses to open the app on
            # anything older with a clear message, instead of a crash.
            "LSMinimumSystemVersion": "13.0",
            "LSApplicationCategoryType": "public.app-category.utilities",
            # A menu bar app: no Dock icon at launch. The app shows one only
            # while its window is open (app/ui/dock.py).
            "LSUIElement": True,
        },
    )
else:
    # Windows: stamp the version into the exe, so Properties › Details and the
    # installed-apps list show the same version as macOS's About and Finder.
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    numbers = tuple(int(part) for part in VERSION.split(".")) + (0,) * 4
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers[:4], prodvers=numbers[:4]),
        kids=[
            StringFileInfo([
                StringTable("040904B0", [
                    StringStruct("CompanyName", "DoubleClick Fixer"),
                    StringStruct("FileDescription", "DoubleClick Fixer"),
                    StringStruct("FileVersion", VERSION),
                    StringStruct("InternalName", "DoubleClickFixer"),
                    StringStruct("LegalCopyright", "© 2026 Arnav Goel. MIT License."),
                    StringStruct("OriginalFilename", "DoubleClickFixer.exe"),
                    StringStruct("ProductName", "DoubleClick Fixer"),
                    StringStruct("ProductVersion", VERSION),
                ])
            ]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )
    if os.environ.get("DCF_ONEDIR") == "1":
        # What the installer ships: the exe beside its libraries, in
        # dist/DoubleClickFixer/ (build_windows.ps1 points --distpath at
        # dist/onedir). The exe keeps its name: the login item, the shortcuts,
        # the installer's taskkill and the updater all find it by it.
        exe = EXE(
            pyz,
            analysis.scripts,
            [],
            exclude_binaries=True,
            name="DoubleClickFixer",
            console=False,
            icon="installer/assets/icon.ico",
            version=version_info,
        )
        collected = COLLECT(exe, analysis.binaries, analysis.datas, name="DoubleClickFixer")
    else:
        # The portable release asset: one executable.
        exe = EXE(
            pyz,
            analysis.scripts,
            analysis.binaries,
            analysis.datas,
            [],
            name="DoubleClickFixer",
            console=False,
            icon="installer/assets/icon.ico",
            version=version_info,
        )
