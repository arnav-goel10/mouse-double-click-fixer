# Releasing

Installed copies of Mouse Double-Click Fixer update themselves from GitHub
Releases. They look at the latest published release, download its
`SHA256SUMS.txt` and the signature beside it, `SHA256SUMS.txt.minisig`, and
install nothing unless that signature comes from one of the release keys built
into the app and names the release's version. The download for their platform
must then match the signed checksums.

So CI never publishes. Pushing a tag makes the release workflow build and
test the release and put it up as a draft. Then, on the Mac where the release
keys live, `tools/sign_release.py publish` checks the draft against what CI
built, signs it and publishes it. Copies never see a draft, and a published
release without a valid signature is refused (General says why), so nothing
reaches users unsigned.

## One-time setup

### The release environment

The macOS code-signing certificate is kept in a GitHub environment called
`release`, which only tags matching `v*.*.*` may use. Once SignPath is set
up, its API token and the three IDs go there too (see
[Distribution](DISTRIBUTION.md#after-acceptance)). The release workflow's
macOS and Windows jobs load the environment only for the push of a tag; a
manual run never does. Create the environment, put the two macOS secrets in
it, and delete the repository-wide copies:

```bash
gh api -X PUT repos/arnav-goel10/mouse-double-click-fixer/environments/release \
  -F 'deployment_branch_policy[protected_branches]=false' -F 'deployment_branch_policy[custom_branch_policies]=true'
gh api -X POST repos/arnav-goel10/mouse-double-click-fixer/environments/release/deployment-branch-policies -f name='v*.*.*' -f type=tag
base64 -i ~/.doubleclick-fixer-signing/signing.p12 | gh secret set MACOS_SIGNING_P12 --env release --repo arnav-goel10/mouse-double-click-fixer
printf %s "$(cat ~/.doubleclick-fixer-signing/p12.password)" | gh secret set MACOS_SIGNING_P12_PASSWORD --env release --repo arnav-goel10/mouse-double-click-fixer
gh secret delete MACOS_SIGNING_P12 --repo arnav-goel10/mouse-double-click-fixer
gh secret delete MACOS_SIGNING_P12_PASSWORD --repo arnav-goel10/mouse-double-click-fixer
```

### The publishing Mac

`publish` runs on the Mac that holds the release keys. Check once that it
has what `publish` needs:

```bash
gh auth status        # signed in to GitHub with access to the repository
csrutil status        # System Integrity Protection must be enabled
clang --version       # Xcode's command line tools, for the injection check
ls -l ~/.doubleclick-fixer-signing/update-keys/   # primary.key and backup.key, readable only by you
```

`tools/sign_release.py` runs on macOS's own `python3`, unless the key has a
password (see [Keys and certificates](#keys-and-certificates)).

## Cutting a release

`python3 tools/sign_release.py --help` prints these steps too.

1. Merge what is being released into `main`:

   ```bash
   git switch main && git pull --ff-only
   git merge --no-ff BRANCH
   ```

2. Set `__version__` in `app/__init__.py` (three numbers, `1.2.3`), and turn
   the top of `CHANGELOG.md` into `## 1.2.3 — YYYY-MM-DD` with today's date.
   That entry becomes the release page's "What's new", so write it for the
   people downloading the app. Check both as the release workflow will, then
   commit and push:

   ```bash
   GITHUB_REF=refs/tags/v1.2.3 python3 -m unittest tests.test_release
   git commit -am "Mouse Double-Click Fixer 1.2.3"
   git push origin main
   ```

3. Tag that commit and push the tag. The release workflow builds it:

   ```bash
   git tag -a v1.2.3 -m "Mouse Double-Click Fixer 1.2.3"
   git push origin v1.2.3
   ```

4. Wait until the tag's run exists (it can take a few seconds to appear),
   then until it finishes. It puts up the draft:

   ```bash
   until run="$(gh run list --workflow release.yml --branch v1.2.3 --event push --limit 1 --json databaseId --jq '.[].databaseId')" && [ -n "$run" ]; do sleep 5; done
   gh run watch "$run" --exit-status
   ```

5. On the publishing Mac, in a checkout of `main` at the tag, check the
   draft, then sign and publish it:

   ```bash
   python3 tools/sign_release.py publish v1.2.3 --dry-run
   python3 tools/sign_release.py publish v1.2.3
   ```

   Both run every check. Only the second signs, uploads
   `SHA256SUMS.txt.minisig` and publishes. A copy with **Check for updates
   automatically** on checks 20 seconds after it starts and then every six
   hours, so a running copy picks the release up within six hours of
   publishing. A copy with that switch off checks only when its owner asks.

Then submit the new version to winget: `python3 tools/winget_manifest.py
1.2.3` writes its manifests (see [Distribution](DISTRIBUTION.md#2-winget)).

A pre-release is for testing. Tag it with a suffix (`v1.1.0-rc.1`) and follow
the same steps, with two differences. `__version__` in `app/__init__.py` stays
the base version, `1.1.0`: the version check compares the tag without its
suffix with it. And `CHANGELOG.md` needs a `## 1.1.0` heading, which needs no
date until the full release; the release page shows that section. `publish`
checks it the same way and publishes it without signing it and without making
it the latest release, so installed copies never offer it.

Publishing a release (not a pre-release) starts the Release pages workflow
(`.github/workflows/release-pages.yml`), which puts a line at the top of every
older release page saying a newer version exists, with a link to it. The rest
of each page stays as it was published.

### What CI checks before a draft exists

The release workflow (`.github/workflows/release.yml`) puts up a draft only
when every one of its jobs passes:

- **Version, changelog and pins** runs `tests/test_release.py`: the tag is
  the version in `app/__init__.py`, the changelog has a dated entry for it,
  the release page builds, and the pins (see [Build tools](#build-tools))
  and the workflows are set up as this page describes.
- **The whole CI workflow** (`ci.yml`; see
  [Checks before a release](#checks-before-a-release)).
- **Windows build** refuses CI's update key (see
  [CI's update key](#cis-update-key)), builds the portable exe and the
  installed folder, which must ship the same third-party notices, checks
  that neither carries CI's key, runs the portable exe's self-test, and
  builds the installer. Once SignPath is set up, a tag's build has the
  executables, then the installer, signed before they become release files,
  so the checksums and their signature cover the signed files; each signing
  request waits for you to approve it in SignPath (see
  [Distribution](DISTRIBUTION.md)).
- **Windows install and upgrade, with the release's own installer** installs
  0.2.6 and 0.5.3, installs the new installer over each while it runs,
  checks that quitting works and an unsigned update is refused, and
  uninstalls. The installed app's self-test must report its TLS going through
  Schannel and a supported OpenSSL, and its folder must hold no Qt OpenSSL
  plugin, none of the OpenSSL files Qt's backend would load
  (`libcrypto-3-x64.dll` and `libssl-3-x64.dll`; Python's own `libcrypto-3.dll`
  and `libssl-3.dll` ship) and no copy of the Universal C Runtime. The old
  installers come from a cache keyed by `tools/old_installers.json`, each
  checked against its SHA-256, so a run that finds the cache doesn't add to the
  Releases' download numbers. Actions caches are scoped to a branch: a run
  restores its own branch's cache and the default branch's, so the first run on
  each branch or tag downloads the installers (later runs there use their own
  cache) until `main` has a run that saved it. The leg that installs a signed update needs a build that
  trusts CI's key, so it runs in `ci.yml` only.
- **macOS build**, on a macOS 26 runner, loads the signing certificate from
  the `release` environment and refuses to build without it. The build
  script checks the signed app (see
  [macOS signing](#macos-signing-and-why-it-matters-for-updates)); the job
  then checks that the app reports the release's version and keeps the
  designated requirement installed copies have.
- **Put the release up as a draft** checks that the files are exactly the six
  listed above `SHA256SUMS.txt` in [The release files](#the-release-files),
  writes `SHA256SUMS.txt` and the release page, and refuses if the tag already
  has a release. A draft is never marked as the latest release. A tag with a
  suffix (`v1.1.0-rc.1`) makes a pre-release.

A manual run of the release workflow, on a branch or a tag, only builds and
tests: it never loads the certificate and never puts up a draft.

### What `publish` checks

`publish` downloads the draft's files and the artifacts of the release
workflow run that built the tag (the push of that tag, at the commit it
points at; `--run ID` names one if more than one did), and refuses unless:

- the draft holds exactly the release's files, each byte for byte the file
  the run built, and matching the digest GitHub lists for it;
- `SHA256SUMS.txt` lists every file with the right hash, and the macOS zip
  holds exactly one app, of the tag's version;
- that app's code signature is valid, and its designated requirement is the
  one installed copies have, so macOS keeps their permission;
- `THIRD_PARTY_NOTICES-macos.md` is the notices file inside that app;
- `tools/macos_injection_check.sh` passes on that app with every leg (see
  [SIP and the injection check](#sip-and-the-injection-check)).

It then signs `SHA256SUMS.txt` with the primary key and the trusted comment
`dcf 1.2.3`, checks the signature against the keys built into the app,
uploads `SHA256SUMS.txt.minisig` and checks the upload, checks that nothing
on the draft changed meanwhile, and publishes the draft as the latest
release. `--dry-run` does every check and signs, uploads and publishes
nothing.

If a run of `publish` stopped after it uploaded the signature, running it
again keeps that signature. When the draft already carries one that verifies
for this version and requirement, `publish` checks it and publishes; it signs
and uploads only when the draft has none. A signature that doesn't verify, or
names another requirement, stops it: delete it from the draft and run `publish`
again.

**Pre-releases are never signed.** `publish` checks a pre-release the same
way, then publishes it without a signature and without making it the latest
release, and refuses one that carries a signature. Installed copies only look
at the latest full release, and couldn't install an unsigned one anyway.

`python3 tools/sign_release.py verify 1.2.3 FOLDER` checks a downloaded,
signed release folder the way the app will. The docstring at the top of
`tools/sign_release.py` lists the steps `publish` takes, for doing them by
hand.

### The release files

| File | For |
| --- | --- |
| `DoubleClickFixer.dmg` | people installing on macOS |
| `DoubleClickFixer-macos.zip` | the macOS updater |
| `DoubleClickFixer-Setup.exe` | people installing on Windows, and the Windows updater |
| `DoubleClickFixer.exe` | the portable Windows copy and its updater |
| `THIRD_PARTY_NOTICES-macos.md` | the licences of what the macOS app bundles; the copy inside the app |
| `THIRD_PARTY_NOTICES-windows.md` | the licences of what both Windows downloads bundle; the copy the installer puts beside the app |
| `SHA256SUMS.txt` | the checksums of every file above |
| `SHA256SUMS.txt.minisig` | the signature over the checksums, added by `publish` |

The release page is `installer/release_notes.md` (downloads, requirements and
first-launch help) followed by the changelog entry, assembled by
`tools/release_notes.py`. To redo a page by hand:

```bash
python3 tools/release_notes.py 1.2.3 arnav-goel10/mouse-double-click-fixer > notes.md
gh release edit v1.2.3 --notes-file notes.md
# an older page, pointing at the latest release:
python3 tools/release_notes.py 1.2.2 arnav-goel10/mouse-double-click-fixer --latest 1.2.3 > notes.md
# the newer-version line on every older page, as the Release pages workflow does
# (without --apply it only prints):
python3 tools/release_notes.py --point-older arnav-goel10/mouse-double-click-fixer --apply
```

### Build tools

Builds and CI install `requirements-build.txt` with
`--require-hashes --only-binary :all:`: every package at a pinned version,
every file checked against its hash, wheels only. It holds the app's
pins from `requirements.txt` plus PyInstaller, its hooks and dmgbuild, and is
generated by uv from `requirements-build.in`. CI's tests install the same
file, so a release is built with exactly what CI tested. After changing
either `requirements.txt` or `requirements-build.in`, regenerate it:

```bash
uv pip compile requirements-build.in --universal --generate-hashes \
  --python-version 3.11 --no-header -o requirements-build.txt
```

`tests/test_release.py` fails if it pins something differently from
`requirements.txt` or `requirements-build.in`, or if anything installs
packages without hashes. Inno Setup is pinned to 6.7.1 in both workflows, and
the jobs check that Chocolatey installed exactly that version. Every action in
the workflows is pinned to a commit. Dependabot proposes updates to the
actions only: it can't regenerate the hashed file, so Python pins are updated
by hand. When Qt changes, check the macOS minimum: it sets
`LSMinimumSystemVersion` in `doubleclick-fixer.spec`.

The macOS job runs on a macOS 26 runner: only its Xcode can compile the layered
app icon (`installer/assets/AppIcon.icon`). An older Xcode still builds the app,
with the flat icon only.

### The build Python and its OpenSSL

Release builds run on python.org's CPython 3.14: the workflows ask
`actions/setup-python` for `"3.14"` with `check-latest`, so they get the
newest 3.14 rather than an older one a runner has cached. The app ships
Python's own OpenSSL. Python's `ssl` and `hashlib` run it on both platforms,
and on macOS Qt's TLS runs it too, for every update check and download.
CPython 3.14.6 and later ship OpenSSL 3.5, a long-term support release
supported until 2030-04-08; python.org's 3.13 still ships OpenSSL 3.0, which
reached end of life on 2026-09-07.

Every build also checks where each binary it collects comes from
(`tools/binary_sources.py`, run by `doubleclick-fixer.spec`): each library,
Python extension and DLL must come from Python itself or from the packages
installed in the environment that builds, or the build fails and names the
file. A runner's PATH can lead to anything. GitHub's Windows runners put a
MySQL OpenSSL and a JDK's copy of the Universal C Runtime there, and both were
once collected.

The self-test's `openssl` check fails a build if the OpenSSL that Python or
Qt runs, or any OpenSSL library file inside the app, is older than
`OPENSSL_FLOOR` in `app/selftest.py` (3.5). OpenSSL makes a long-term support
release every other April, the next in April 2027. Once python.org's CPython
ships it, move the builds to that Python and raise the floor to it; in any
case, before April 2030. `tests/test_release.py` fails if a job that builds
the app asks for another Python. The unit tests still run on Python 3.11 to
3.14; run from source, the check only reports.

### SIP and the injection check

`tools/macos_injection_check.sh` runs the app's self-test four times, each
with canary libraries and programs named in its environment, and fails if any
canary is loaded or run. The legs are `dyld` (`DYLD_INSERT_LIBRARIES`), `cwd`
(a folder holding a canary under each bare name Qt's OpenSSL backend asks the
loader for), `openssl` (`OPENSSL_CONF`) and `path` (`PATH`, `BASH_ENV`, `ENV`
and an exported function). The dyld and cwd legs mean something only with
System Integrity Protection on: with it off, dyld loads
`DYLD_INSERT_LIBRARIES` into any app, hardened runtime or not, and need not
refuse a library by relative path. SIP is off on GitHub's macOS runners, so
the build there skips those two legs, with a warning, and runs the openssl and
path legs. `publish` runs the check with `--require-sip` on the release's own
app, which runs every leg and fails unless SIP is on.

### CI's update key

Installed copies take only updates signed with the release keys, which never
leave the publishing Mac. So CI's Windows install test (the `windows-install`
job in `ci.yml`) makes a key pair for that run (`tools/ci_update_key.py`) and
builds the app to trust its public half, so the test can sign a stand-in
update and install it. The key is built in only when `DCF_CI_UPDATE_KEY`
names it: the spec then freezes a start-up hook into the Windows build that
sets `app.build_flags.CI_UPDATE_KEY`, and only a frozen Windows app trusts
that key. macOS builds ignore the variable. The release workflow's Windows
job refuses to build with it set, and `tools/ci_update_key.py check` confirms
that neither Windows build it made carries the key or the hook. When the
release workflow runs `ci.yml`, that job's builds still trust CI's key; they
are tested and thrown away, never released.

## Keys and certificates

Everything that signs a release is in `~/.doubleclick-fixer-signing/` on the
maintainer's Mac:

- `update-keys/primary.key` and `update-keys/backup.key`: the minisign
  release keys. Their public halves are `RELEASE_KEYS` in `app/updater.py`
  (copies in `tools/keys/`). Releases are signed with the primary key; the
  backup is built into every copy so the primary can be replaced without
  stranding anyone. These keys never go to GitHub or CI.
- `signing.keychain-db`, `keychain.password`, `signing.p12` and
  `p12.password`: the macOS code-signing certificate, "DoubleClick Fixer
  Signing", the name it was made with before the app was renamed (a renamed
  certificate would be a new one, and every Mac would have to allow the app
  again). CI has it too, as the secrets `MACOS_SIGNING_P12` (the `.p12`,
  base64-encoded) and `MACOS_SIGNING_P12_PASSWORD` in the `release`
  environment (see [One-time setup](#one-time-setup)); the release workflow
  refuses to build a tagged macOS release without them.

Back the folder up somewhere offline, and keep a copy of the backup key off
this Mac. The keys are files readable only by you, without a password;
`minisign -C -s <key>` adds one, which `sign_release.py` then asks for. A key
with a password needs Homebrew's `python3`: macOS's own `/usr/bin/python3`
(3.9) has no `hashlib.scrypt` to decrypt it.

- Losing the certificate means the next release is signed differently, and
  every user has to allow the app in Privacy & Security again.
- Losing both release keys means installed copies can never be updated
  again; they have to be reinstalled by hand.
- Anyone who gets a release key can sign updates. If one leaks, sign the
  next release with the other (`publish --key`), and build it with the
  leaked key taken out of `RELEASE_KEYS` and a new backup
  (`sign_release.py keygen`) put in. A new key isn't trusted until it is in
  `RELEASE_KEYS`, and then only by copies built with it.

Never sign test material with the release keys. A real signature over a
made-up `SHA256SUMS.txt` for a newer version would be accepted by every
installed copy. The tests use throwaway keys, and CI's Windows update test
makes a key for that run only (see [CI's update key](#cis-update-key)).

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
(`installer/runtime_hooks/scrub_env.py`) removes the environment variables
that would make those libraries load code from elsewhere, points
`OPENSSL_CONF` at an empty file, and pins `PATH`.

`installer/build_macos.sh` fails unless `codesign` reports the runtime flag.
Before packaging, it then runs the signed app's `--self-test` twice, offscreen
and with the real Cocoa platform plugin; runs `tools/macos_injection_check.sh`
(see [SIP and the injection check](#sip-and-the-injection-check)); and checks
that the `THIRD_PARTY_NOTICES.md` it ships is exactly what its own files call
for (`tools/make_notices.py --bundle --check`). Any failure stops the build.

The certificate is self-signed, which is enough for the permission to carry
over. A Developer ID certificate would also remove the Gatekeeper step on first
launch and allow notarization. Moving to one changes the designated
requirement, so users grant the permission once more, and installed copies
accept the new requirement only from a release signed with it named. In that
release, change `APP_REQUIREMENT` in `tools/sign_release.py` and the
requirement the release workflow checks for (a test keeps the two the same),
and publish it with:

```bash
python3 tools/sign_release.py publish v1.3.0 --requirement '<new requirement>'
```

where the requirement is exactly what
`codesign -d -r- "Mouse Double-Click Fixer.app"` prints after
`designated =>`. It must be certificate-based.

## Checks before a release

CI runs on every push to `main` and every pull request, and the release
workflow runs it in full before it puts up a draft:

- **Unit tests** on Windows and macOS 14, Python 3.11 to 3.14. On macOS, where
  the runner allows it, two of them also start the real event tap and stop it
  again; neither posts any input.
- **macOS event tap end-to-end**, on macOS 14 and macOS 26, posts real clicks
  and moves through the real filter's event tap and checks what applications
  receive (`tests/test_macos_tap_e2e.py`, `DCF_E2E=1`).
- **Windows hook end-to-end** sends real clicks and moves through the real
  low-level hook (`DCF_E2E=1`).
- **Windows install, quit and update end-to-end** installs 0.2.6 and 0.5.3,
  leaves each running and installs the new build over it, opens it twice,
  quits it while it is starting, runs a full signed in-app update with CI's
  key, checks that an unsigned one is refused, and uninstalls
  (`tools/windows_install_e2e.ps1`).

## Packaging locally

```bash
bash installer/build_macos.sh       # app, DMG and updater zip in dist/
.\installer\build_windows.ps1       # portable exe and installed folder in dist\;
                                    # then compile installer\windows.iss
                                    # (see installer/README.md)
```

Build outside a synced folder if you can; iCloud Drive adds file attributes
that code signing rejects, which is why the script signs in a temporary folder.

A build that matches a release needs python.org's CPython 3.14 (see
[The build Python and its OpenSSL](#the-build-python-and-its-openssl)). On a
Mac, install it from python.org and build in a virtualenv made from it:

```bash
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m venv .venv-build
. .venv-build/bin/activate && bash installer/build_macos.sh
```

Other Pythons fail the macOS build. Homebrew's loads OpenSSL from Homebrew's
own folder, outside Python, which the build refuses
(`tools/binary_sources.py`). uv's CPython links OpenSSL into Python itself,
so the app has no OpenSSL library for Qt's TLS to load and the self-test's
`tls` check fails. python.org's 3.13, and 3.14 before 3.14.6, ship OpenSSL
3.0, which the `openssl` check refuses.
