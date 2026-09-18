# PyInstaller build recipe for both platforms.
import sys

VERSION = "0.2.0"

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

hiddenimports = []
if sys.platform == "darwin":
    # The event tap is reached through PyObjC at runtime.
    from PyInstaller.utils.hooks import collect_submodules

    hiddenimports += collect_submodules("Quartz") + collect_submodules("AppKit")

analysis = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
)

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
        name="DoubleClickFixer.app",
        icon="installer/assets/icon.icns",
        bundle_identifier="com.doubleclickfixer.app",
        info_plist={
            "CFBundleName": "DoubleClick Fixer",
            "CFBundleDisplayName": "DoubleClick Fixer",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.utilities",
        },
    )
else:
    # Windows: one portable executable; the installer copies it as is.
    exe = EXE(
        pyz,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        [],
        name="DoubleClickFixer",
        console=False,
        icon="installer/assets/icon.ico",
    )
