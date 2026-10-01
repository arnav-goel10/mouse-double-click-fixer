# Releasing

Installed copies of DoubleClick Fixer update themselves from GitHub Releases:
they check the latest release a few seconds after launch and every six hours,
download the file for their platform, verify it against the release's
`SHA256SUMS.txt`, replace themselves and reopen. People can turn automatic
installs off in **General › Software update** and check by hand instead.

## Cutting a release

1. Update `app/__init__.py`, and add a `## <version> — <date>` entry at the
   top of `CHANGELOG.md`. That entry becomes the release page's "What's new",
   so write it for the people downloading the app. Commit.
2. Tag the same version and push the tag:

```bash
git tag -a v1.2.3 -m "DoubleClick Fixer 1.2.3"
git push origin v1.2.3
```

The release page is `installer/release_notes.md` (downloads and first-launch
help) followed by the changelog entry, assembled by `tools/release_notes.py`.
Publishing also adds a "newer version available" note to every older release
page. To redo one page by hand:

```bash
python3 tools/release_notes.py 1.2.3 arnav-goel10/doubleclick-fixer > notes.md
gh release edit v1.2.3 --notes-file notes.md
```

The release workflow checks that the tag matches `app/__init__.py`, runs the
tests on both platforms, builds and signs everything, and publishes:

| File | For |
| --- | --- |
| `DoubleClickFixer.dmg` | people installing on macOS |
| `DoubleClickFixer-macos.zip` | the macOS updater |
| `DoubleClickFixer-Setup.exe` | people installing on Windows, and the Windows updater |
| `DoubleClickFixer.exe` | the portable Windows copy and its updater |
| `SHA256SUMS.txt` | the updater's integrity check |

The macOS job runs on a macOS 26 runner: only its Xcode can compile the layered
app icon (`installer/assets/AppIcon.icon`). An older Xcode still builds the app,
with the flat icon only.

## Checks before a release

CI runs on every push. Besides the unit tests on both platforms, two jobs
exercise a real Windows machine:

- **Windows hook end-to-end** sends clicks through the real low-level hook.
- **Windows install, quit and update end-to-end** installs 0.2.6 and leaves it
  running, installs the new build over it, opens it twice, quits it while it
  is starting, runs a full in-app update and uninstalls
  (`tools/windows_install_e2e.ps1`).

macOS has no equivalent in CI: GitHub's Macs can't grant Accessibility, which
the event tap needs. Before tagging, try a drag and a double-click on a Mac
with the new build.

## macOS signing, and why it matters for updates

macOS stores the Accessibility permission against the app's code signature.
Every release is therefore signed with the same certificate
("DoubleClick Fixer Signing"), so an update keeps the permission the user
already granted. The updater also refuses any macOS update whose signature
does not match the installed app's, which protects against tampered
downloads.

The certificate lives in two places:

- **Locally**, in `~/.doubleclick-fixer-signing/` (its own keychain file and
  a password-protected `signing.p12`). `installer/build_macos.sh` uses it
  automatically. Back this folder up somewhere safe: losing it means the next
  release would be signed differently and every user would have to grant
  Accessibility again.
- **In CI**, as two repository secrets, `MACOS_SIGNING_P12` (the `.p12`,
  base64-encoded) and `MACOS_SIGNING_P12_PASSWORD`. The release workflow
  refuses to publish a tagged macOS build without them.

The certificate is self-signed, which is enough for the permission to carry
over. An Apple Developer ID certificate would additionally remove the
"unidentified developer" prompt on first launch; switching to one later means
users grant Accessibility one more time.

## Packaging locally

```bash
bash installer/build_macos.sh       # app, DMG and updater zip in dist/
.\installer\build_windows.ps1       # portable exe; then compile installer\windows.iss
                                    # with ISCC "/DAppVersion=<version>" (see installer/README.md)
```

Build outside a synced folder if you can; iCloud Drive adds file attributes
that code signing rejects, which is why the script signs in a temporary folder.
