import sys

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("PIL") + collect_submodules("pystray")
if sys.platform == "darwin":
    hiddenimports += collect_submodules("Quartz")


analysis = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
)

pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="DoubleClickFixer",
    console=False,
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="DoubleClickFixer.app",
        bundle_identifier="com.doubleclickfixer.app",
    )
