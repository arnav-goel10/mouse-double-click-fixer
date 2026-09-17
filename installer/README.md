# Packaging

Build Windows from Windows:

```powershell
.\installer\build_windows.ps1
```

Compile `installer/windows.iss` with Inno Setup to produce the Windows installer. The installer offers an optional startup shortcut.

From the project root, after installing Inno Setup:

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "installer\windows.iss"
```

The installer is written to the `installer\Output` folder.

Build macOS from macOS:

```bash
bash installer/build_macos.sh
```

The macOS script produces a universal app bundle and DMG. macOS users must grant Accessibility permission when enabling the fix. Code signing and notarization require the publisher's Apple Developer identity and are intentionally left to release CI.

The repository also includes a GitHub Actions macOS build. Push the repository to GitHub, open **Actions**, run **Build macOS**, then download the `DoubleClickFixer-macos` artifact.

## Uninstall cleanup

The Windows installer includes an uninstaller that removes the application, shortcut, startup registry entry, and saved settings. On macOS, run `bash installer/uninstall_macos.sh` before deleting the app; it removes the app, LaunchAgent, saved settings, and preference files.
