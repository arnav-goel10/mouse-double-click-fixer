# Packaging

Release builds come from CI when a version tag is pushed; see
[docs/RELEASING.md](../docs/RELEASING.md). These commands are for building by hand.

## macOS

```bash
bash installer/build_macos.sh
```

Builds `dist/DoubleClick Fixer.app` (Apple silicon), the DMG and the zip the
in-app updater downloads. It signs with the project's self-signed certificate
from `~/.doubleclick-fixer-signing` when that exists, and ad-hoc otherwise. An
ad-hoc copy is fine for trying things out but not for installing: macOS ties the
Accessibility permission to the certificate, so updates to it would ask again.
The same goes for the **Build macOS** Actions artifact, which is ad-hoc signed.

## Windows

```powershell
.\installer\build_windows.ps1
$version = python -c "import app; print(app.__version__)"
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "/DAppVersion=$version" "installer\windows.iss"
```

The first command builds the portable `dist\DoubleClickFixer.exe`; the second
wraps it in the installer, written to `installer\Output`. Pass `/DAppVersion`,
or the installer reports a placeholder version.

## Uninstalling

The Windows uninstaller closes a running copy, then removes the app, its
shortcuts, the startup entry and saved settings. On macOS,
`bash installer/uninstall_macos.sh` quits the app and removes it, its login
item, settings and Accessibility permission.
