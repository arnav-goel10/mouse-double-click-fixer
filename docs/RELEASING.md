# Releasing

Installed copies of DoubleClick Fixer update themselves from GitHub Releases.
They look at the latest published release, download its `SHA256SUMS.txt` and
the signature beside it, `SHA256SUMS.txt.minisig`, and install nothing unless
that signature comes from one of the release keys built into the app and
names the release's version. The download for their platform must then match
the signed checksums.

So CI builds a release and saves it as a draft, the maintainer signs it on
the Mac where the release keys live, and only then is it published.
Copies never see a draft, and a published release without a valid signature
is refused (General says why), so nothing reaches users unsigned.

## Cutting a release

1. Update `__version__` in `app/__init__.py` (three numbers, `1.2.3`), and add
   a `## 1.2.3 — <date>` entry at the top of `CHANGELOG.md`. That entry
   becomes the release page's "What's new", so write it for the people
   downloading the app. Commit.
2. Tag the same version and push the tag:

   ```bash
   git tag -a v1.2.3 -m "DoubleClick Fixer 1.2.3"
   git push origin v1.2.3
   ```

3. The release workflow (`.github/workflows/release.yml`) runs the whole CI
   workflow alongside the builds and publishes nothing unless it passes. It
   checks that the tag matches `app/__init__.py`, builds and code-signs both
   platforms with pinned build tools, has the built macOS app check itself
   (`--self-test`), and creates a **draft** release with the files below and
   their `SHA256SUMS.txt`.
4. Try the draft's build on a Mac: a drag, a double-click, a click followed
   by a quick move. CI can't send clicks through a real macOS event tap.
5. Sign the release on your Mac and upload the signature:

   ```bash
   gh release download v1.2.3 --dir ~/dcf-release-1.2.3
   python3 tools/sign_release.py sign 1.2.3 ~/dcf-release-1.2.3
   gh release upload v1.2.3 ~/dcf-release-1.2.3/SHA256SUMS.txt.minisig
   ```

   `sign` refuses unless every file in the folder is listed in
   `SHA256SUMS.txt` with the right hash, nothing listed is missing, and the
   macOS zip holds exactly one app of that version. It signs with the trusted
   comment `dcf 1.2.3` and checks the result against the keys built into the
   app. `python3 tools/sign_release.py verify 1.2.3 FOLDER` checks a signed
   folder the way the app will. The tool runs on macOS's own `python3`.
6. Publish the draft:

   ```bash
   gh release edit v1.2.3 --draft=false --latest
   ```

   Installed copies pick it up at their next check, within six hours. Never
   publish a release before its signature is uploaded: every copy that
   checks in that window reports "This update isn't signed" until it is.

The release page is `installer/release_notes.md` (downloads, requirements and
first-launch help) followed by the changelog entry, assembled by
`tools/release_notes.py`. Older release pages carry a note pointing to the
latest one. To redo one page by hand:

```bash
python3 tools/release_notes.py 1.2.3 arnav-goel10/doubleclick-fixer > notes.md
gh release edit v1.2.3 --notes-file notes.md
# an older page, pointing at the latest release:
python3 tools/release_notes.py 1.2.2 arnav-goel10/doubleclick-fixer --latest 1.2.3 > notes.md
```

| File | For |
| --- | --- |
| `DoubleClickFixer.dmg` | people installing on macOS |
| `DoubleClickFixer-macos.zip` | the macOS updater |
| `DoubleClickFixer-Setup.exe` | people installing on Windows, and the Windows updater |
| `DoubleClickFixer.exe` | the portable Windows copy and its updater |
| `THIRD_PARTY_NOTICES.md` | the licences of what the builds bundle |
| `SHA256SUMS.txt` | the checksums of every file above |
| `SHA256SUMS.txt.minisig` | the signature over the checksums, uploaded by you |

The macOS job runs on a macOS 26 runner: only its Xcode can compile the layered
app icon (`installer/assets/AppIcon.icon`). An older Xcode still builds the app,
with the flat icon only.

Release builds use pinned versions of PyInstaller, its hooks, dmgbuild and
Inno Setup, alongside the Qt and PyObjC versions pinned in
`requirements.txt`, so a release is built with the same tools CI tested.
Dependabot proposes upgrades. When Qt changes, check the macOS minimum: it
sets `LSMinimumSystemVersion` in `doubleclick-fixer.spec`.

## Keys and certificates

Everything that signs a release is in `~/.doubleclick-fixer-signing/` on the
maintainer's Mac:

- `update-keys/primary.key` and `update-keys/backup.key`: the minisign
  release keys. Their public halves are `RELEASE_KEYS` in `app/updater.py`
  (copies in `tools/keys/`). Releases are signed with the primary key; the
  backup is built into every copy so the primary can be replaced without
  stranding anyone. These keys never go to GitHub or CI.
- `signing.keychain-db`, `keychain.password` and `signing.p12`: the macOS
  code-signing certificate, "DoubleClick Fixer Signing". CI has it too, as
  the repository secrets `MACOS_SIGNING_P12` (the `.p12`, base64-encoded) and
  `MACOS_SIGNING_P12_PASSWORD`; the release workflow refuses to build a
  tagged macOS release without them.

Back the folder up somewhere offline, and keep a copy of the backup key off
this Mac. The keys are files readable only by you, without a password;
`minisign -C -s <key>` adds one, which `sign_release.py` then asks for.

- Losing the certificate means the next release is signed differently, and
  every user has to allow the app in Privacy & Security again.
- Losing both release keys means installed copies can never be updated
  again; they have to be reinstalled by hand.
- Anyone who gets a release key can sign updates. If one leaks, sign the
  next release with the other, and build it with the leaked key taken out of
  `RELEASE_KEYS` and a new backup (`sign_release.py keygen`) put in.
  A new key isn't trusted until it is in `RELEASE_KEYS`, and then only by
  copies built with it.

Never sign test material with the release keys. A real signature over a
made-up `SHA256SUMS.txt` for a newer version would be accepted by every
installed copy. The tests use throwaway keys, and CI's Windows update test
makes a key for that run only (`tools/ci_update_key.py`); the release workflow
refuses a build that carries it.

## macOS signing, and why it matters for updates

macOS stores the app's permission (Device Control and Data Access, or
Accessibility on macOS 26 and earlier) against its code signature's
designated requirement. Every release is signed with the same certificate, so
the requirement stays the same and an update keeps the permission the user
granted. The updater refuses a macOS update whose requirement differs from the
installed app's.

The app is signed with the hardened runtime and one entitlement,
`com.apple.security.cs.disable-library-validation`
(`installer/entitlements.plist`): the self-signed certificate has no Team ID,
so library validation would refuse the bundled Python and Qt. A runtime hook
(`installer/runtime_hooks/scrub_env.py`) clears the environment variables that
would make those libraries load code from elsewhere. `installer/build_macos.sh`
fails unless `codesign` reports the runtime flag, and runs the signed app's
`--self-test` before packaging. `tools/macos_injection_check.sh` checks that a
built app loads no code named in its environment.

The certificate is self-signed, which is enough for the permission to carry
over. A Developer ID certificate would also remove the Gatekeeper step on first
launch and allow notarization. Moving to one changes the designated
requirement, so users grant the permission once more, and installed copies
accept the new requirement only from a release signed with it named:

```bash
python3 tools/sign_release.py sign 1.3.0 FOLDER --requirement '<new requirement>'
```

where the requirement is exactly what `codesign -d -r- "DoubleClick Fixer.app"`
prints after `designated =>`. It must be certificate-based.

## Checks before a release

CI runs on every push to `main` and every pull request, and the release
workflow runs it again:

- **Unit tests** on Windows and macOS, Python 3.11 to 3.13. On macOS, where the
  runner allows it, they also create and remove a real pass-through event tap;
  nothing is sent through it.
- **Windows hook end-to-end** sends real clicks and moves through the real
  low-level hook (`DCF_E2E=1`).
- **Windows install, quit and update end-to-end** installs 0.2.6 and 0.5.3,
  leaves each running and installs the new build over it, opens it twice,
  quits it while it is starting, runs a full signed in-app update, checks that
  an unsigned one is refused, and uninstalls (`tools/windows_install_e2e.ps1`).

## Packaging locally

```bash
bash installer/build_macos.sh       # app, DMG and updater zip in dist/
.\installer\build_windows.ps1       # portable exe and installed folder in dist\;
                                    # then compile installer\windows.iss
                                    # (see installer/README.md)
```

Build outside a synced folder if you can; iCloud Drive adds file attributes
that code signing rejects, which is why the script signs in a temporary folder.
