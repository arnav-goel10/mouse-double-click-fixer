# Releasing

1. Confirm `main` is clean and CI is green.
2. Update `app/__init__.py` and `CHANGELOG.md`.
3. Commit the version change.
4. Create and push an annotated tag:

```bash
git tag -a v0.1.1 -m "Release v0.1.1"
git push origin main
git push origin v0.1.1
```

The release workflow tests both platforms, builds the Windows portable executable and Inno Setup installer, builds the macOS app and DMG, and publishes a GitHub Release with generated notes.

## Local packaging

Windows:

```powershell
.\installer\build_windows.ps1
```

macOS:

```bash
bash installer/build_macos.sh
```

Unsigned builds may show platform security warnings. Production signing and notarization require the publisher's platform certificates and secrets configured in GitHub Actions.
