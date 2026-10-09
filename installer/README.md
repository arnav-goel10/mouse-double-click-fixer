# Packaging

Release builds come from CI when a version tag is pushed; see
[docs/RELEASING.md](../docs/RELEASING.md). These commands are for building by
hand.

The builds run on macOS 13 or later on Apple silicon, and on 64-bit Windows 10
version 1809 or later or Windows 11. Both minimums come from Qt 6.11.

Build with python.org's Python 3.14 (3.14.6 or later), as releases are: the
app ships Python's OpenSSL, and its self-test fails a build whose OpenSSL is
older than 3.5. Homebrew's and uv's Pythons don't make a working macOS build;
[Packaging locally](../docs/RELEASING.md#packaging-locally) says why.

## macOS

```bash
bash installer/build_macos.sh
```

Builds `dist/Mouse Double-Click Fixer.app` (Apple silicon), the DMG and the
zip the in-app updater downloads. It signs with the hardened runtime and the
one entitlement in `entitlements.plist`, using the project's self-signed
certificate from `~/.doubleclick-fixer-signing` when that exists, and ad-hoc
otherwise. It fails unless the signature carries the runtime flag. Before
packaging it runs the signed app's `--self-test` offscreen and again with the
Cocoa platform plugin, runs `tools/macos_injection_check.sh` (which needs
clang, from the Xcode Command Line Tools), and checks the app's
`THIRD_PARTY_NOTICES.md` against its files; any failure stops the build.

An ad-hoc copy is fine for trying things out but not for installing: macOS
ties the app's permission to the certificate, so updates to it would ask
again. The same goes for the **Build macOS** Actions artifact, which is
ad-hoc signed.

The layered icon for macOS 26 and later (`assets/AppIcon.icon`) needs the
Xcode that comes with macOS 26; with an older one the build uses the flat
icon only.

## Windows

```powershell
.\installer\build_windows.ps1
$version = python -c "import app; print(app.__version__)"
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "/DAppVersion=$version" "installer\windows.iss"
```

The first command builds two things: the portable `dist\DoubleClickFixer.exe`
(one file, which unpacks itself at each launch) and
`dist\onedir\DoubleClickFixer\`, the folder the installer ships. The second
wraps that folder in the installer, written to `installer\Output`. Pass
`/DAppVersion`, or the installer reports a placeholder version.

The installer installs for the current user without administrator rights,
refuses Windows older than 10 version 1809 and machines that can't run 64-bit
x64 apps, and puts `THIRD_PARTY_NOTICES.md` in the install folder. Before it
replaces files it asks a running copy to quit through the installed one, and
ends it if that doesn't work within 20 seconds. That step runs in
PowerShell's Constrained Language Mode, as it would on a PC with application
control, and waits with `Wait-Process`. An installed copy from before 0.2.7,
or an installed folder build (1.0 and later) that has lost its `_internal`
folder, can't be asked, and is ended with `taskkill`.

## Uninstalling

The Windows uninstaller closes a running copy, then removes the app, its
shortcuts, the startup entry, its settings and its log. On macOS,
`bash installer/uninstall_macos.sh` quits the app and waits until it has
exited (it stops with a message if the app won't quit), then removes it, its
login item, settings and permission.
