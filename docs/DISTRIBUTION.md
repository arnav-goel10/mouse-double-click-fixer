# Distribution: Windows code signing, winget and the Microsoft Store

What the repository already does for each channel, and the steps only the
owner can take. Nothing here creates an account or submits anything; each of
those steps is yours.

## What blocks what

| Channel | Ready in the repository | Blocked by | Your steps |
| --- | --- | --- | --- |
| Signed Windows downloads | `release.yml` signs through SignPath once it is set up; until then it builds unsigned, as before | SignPath Foundation accepting the project | [Apply](#the-application), then [set it up](#after-acceptance) |
| winget | `tools/winget_manifest.py` writes and checks the manifests | Nothing: a published release is enough. Signing isn't required | [Submit](#2-winget) the first version, then each new one |
| Microsoft Store, existing installer (EXE) | The installer installs silently, from a versioned URL | Every program file inside it must be signed by a trusted CA. SignPath can sign only ours, not the Python and Qt libraries or Inno Setup's uninstaller | [See the checklist](#route-a-the-existing-installer-exe) |
| Microsoft Store, MSIX | Nothing yet | Packaging work in the app (no blocker outside it) | [See route B](#route-b-msix) |

A sensible order: apply to SignPath now, since its review takes the longest
and the Store depends on it. Submit 1.0.0 to winget once it is published.
Leave the Store for last.

## 1. Code signing with SignPath Foundation

[SignPath Foundation](https://signpath.org) gives open-source projects a
free code-signing certificate. The private key stays in SignPath's hardware
security module. The certificate is issued to SignPath Foundation, so
Windows shows SignPath Foundation as the publisher. SignPath signs only what
GitHub Actions built from this repository; it checks that origin itself.

### How the release workflow signs

The Windows job in `.github/workflows/release.yml`:

1. builds the portable exe and the installed folder, as before;
2. decides whether to sign. It signs only the push of a release tag, and
   only when the secret `SIGNPATH_API_TOKEN` and the variables
   `SIGNPATH_ORGANIZATION_ID`, `SIGNPATH_PROJECT_SLUG` and
   `SIGNPATH_POLICY_SLUG` are all set. With none set it logs "Not signed"
   and goes on. With only some set, it stops and names what is missing. A
   manual run never signs;
3. uploads the two executables (`portable/DoubleClickFixer.exe` and
   `installed/DoubleClickFixer.exe`) as the artifact
   `unsigned-windows-executables`. SignPath signs them under the artifact
   configuration `windows-executables`, and the job puts them back in
   place;
4. compiles the installer around the signed executable. SignPath can't sign
   files inside an Inno Setup installer, so the executables go first;
5. uploads the installer as `unsigned-windows-installer`. SignPath signs it
   under `windows-installer`, and the job puts it back;
6. checks every build, signed or not, for the product name and version
   SignPath will enforce. When signing ran, it also checks that each of the
   three files carries a valid signature. It then lists every program file
   in the installed folder with its signature status.

Only then are the release files collected. The `DoubleClickFixer-windows`
artifact, the draft, `SHA256SUMS.txt` and its minisign signature all cover
the signed files, and `tools/sign_release.py publish` still checks that the
draft is byte for byte what the run built. The install test
(`windows-e2e`) runs the signed installer. The unsigned uploads are never
release files: `publish` doesn't download them.

Each release makes two signing requests, and SignPath Foundation requires a
person to approve each one. The job waits up to 30 minutes for each
approval. A request that is denied or times out fails the Windows job, so
no draft goes up; re-run the job after approving.

`tools/signpath/windows-executables.xml` and
`tools/signpath/windows-installer.xml` are the two artifact configurations.
`tests/test_release.py` checks that they match what the job uploads.

### Eligibility, against the [terms](https://signpath.org/terms)

| Condition | This project |
| --- | --- |
| OSI-approved licence, no commercial dual-licensing | MIT. The app bundles Qt through PySide6 (LGPL-3.0), which The Qt Company also sells under a commercial licence. The terms don't say whether that counts; mention it in the application |
| No malware or potentially unwanted programs | None. The app filters mouse input and never invents a click |
| No proprietary components | Python, Qt/PySide6 and the other bundled packages are open source (`THIRD_PARTY_NOTICES-windows.md`) |
| Actively maintained, already released in the form to be signed | Releases since 0.2.0 (2026-09-19). Each ships `DoubleClickFixer-Setup.exe` and `DoubleClickFixer.exe` |
| Functionality described on the download page | The README and every release page |
| The signing team is the development team and owns the repository | One maintainer: Arnav Goel |
| Multi-factor authentication for GitHub and SignPath | Yes for GitHub; turn it on for SignPath when the account exists |
| Binaries built verifiably from source | `release.yml` on GitHub-hosted runners, from the tag. Every Python package is pinned by hash (`requirements-build.txt`), and Inno Setup is pinned to 6.7.1 |
| Manual approval of every release | Two requests per release, both approved by you |
| Product name and version enforced by metadata restrictions | Both artifact configurations require product name "Mouse Double-Click Fixer" and the release's version |
| Only your own code signed | Only the three files built here. The Python and Qt libraries beside the installed exe are never sent to SignPath |
| Privacy | No telemetry. The only network use is the update check and download from GitHub, which **General › Check for updates automatically** turns off (README, [Privacy](../README.md#privacy)) |
| System changes announced; an uninstaller | Start at login is off unless chosen. The installer registers an uninstaller; the portable exe is one file to delete |
| A "Code signing policy" on the home page | **To do once accepted:** add [the section below](#the-code-signing-policy-section) to the README and the release page |

SignPath Foundation decides on each application and needn't accept any. The
project is young (first release 2026-09-19); the terms set no minimum age
or download count.

### The application

Apply at <https://signpath.org/apply>. The form loads from HubSpot and
couldn't be read in advance, so the text below covers what applications
usually ask for. Paste the parts the form wants.

> **Project:** Mouse Double-Click Fixer
>
> **Repository:** https://github.com/arnav-goel10/mouse-double-click-fixer
> (public; the only repository; issues and releases on GitHub)
>
> **Licence:** MIT (`LICENSE` in the repository). Bundled third-party
> components are open source: CPython (PSF), Qt 6 via PySide6 (LGPL-3.0),
> and others listed in `THIRD_PARTY_NOTICES-windows.md` on every release.
>
> **Downloads:** https://github.com/arnav-goel10/mouse-double-click-fixer/releases
> (free; no account, no payment)
>
> **What it does:** a tray app for Windows 10 1809+/11 and macOS 13+ that
> fixes mice whose worn switches send two clicks for one ("switch bounce"
> or "chatter"). It filters the extra clicks with a low-level mouse hook and
> leaves real double-clicks alone. No telemetry. Its only network use is the
> update check and download from the project's GitHub Releases, verified
> with a minisign signature and switchable off.
>
> **What we'd sign, Windows only:**
> - `DoubleClickFixer-Setup.exe`: Inno Setup 6.7.1 installer, per-user,
>   no elevation
> - `DoubleClickFixer.exe`: the portable build (PyInstaller one-file)
> - `DoubleClickFixer.exe` inside the installed folder (PyInstaller
>   one-folder), signed before the installer is compiled around it
>
> All three carry product name "Mouse Double-Click Fixer" and the release's
> version in their version resources. We don't ask to sign the upstream
> Python or Qt libraries.
>
> **Build:** GitHub Actions on GitHub-hosted `windows-latest` runners
> (`.github/workflows/release.yml`), from a pushed `v*.*.*` tag only, with
> python.org CPython 3.14 and every package pinned by hash. The workflow
> already contains the steps for SignPath's official GitHub action (pinned
> by commit). They stay inactive until the API token and IDs are configured.
>
> **Team:** Arnav Goel (https://github.com/arnav-goel10), sole maintainer
> and owner of the repository: author, reviewer of outside contributions,
> and approver of every signing request. GitHub account protected by
> two-factor authentication.
>
> **Releases so far:** 0.2.0 to 0.5.3 (since 2026-09-19); 1.0.0 is next.
> macOS builds are signed separately and aren't part of this request.

### After acceptance

SignPath walks accepted projects through onboarding; ask them where these
steps differ.

1. **SignPath:**
   - Turn on multi-factor authentication.
   - Have a project for Mouse Double-Click Fixer linked to the repository,
     with the trusted build system "GitHub.com" added and linked to it.
   - Create two artifact configurations whose slugs are exactly
     `windows-executables` and `windows-installer`, pasting the contents of
     `tools/signpath/windows-executables.xml` and
     `tools/signpath/windows-installer.xml`.
   - Set up a signing policy with you as its approver. Its slug is
     `SIGNPATH_POLICY_SLUG`. If the policy can be limited to a branch or
     ref, limit it to the release tags.
   - Create a CI user with the submitter role on that policy, and generate
     its API token. The token is shown only once.
2. **GitHub:** put the token in the `release` environment, which only tags
   may use, and the three IDs beside it:

   ```bash
   repo=arnav-goel10/mouse-double-click-fixer
   gh secret set SIGNPATH_API_TOKEN --env release --repo $repo        # paste the token
   gh variable set SIGNPATH_ORGANIZATION_ID --env release --repo $repo --body '<organization id>'
   gh variable set SIGNPATH_PROJECT_SLUG --env release --repo $repo --body '<project slug>'
   gh variable set SIGNPATH_POLICY_SLUG --env release --repo $repo --body '<signing policy slug>'
   ```

3. **README and release page:** add the code signing policy below.
4. **Try it on a pre-release.** Tag `v1.0.1-rc.1` (or the next
   pre-release), approve the two requests in SignPath as the run reaches
   them, and check the Windows job's "Check what SignPath checks, and every
   signature" step: all three files `Valid`, signed by SignPath Foundation.
   Then delete the draft and the tag. The first real signed release is the
   next tag after that.

To stop signing, delete the secret: the next release logs "Not signed" and
goes out unsigned.

### The code signing policy section

The terms require it on the home page, under that name, and on download or
release pages. For the README, below **Privacy**:

```markdown
## Code signing policy

Free code signing on Windows provided by [SignPath.io](https://about.signpath.io),
certificate by [SignPath Foundation](https://signpath.org).

- Committers and reviewers: [Arnav Goel](https://github.com/arnav-goel10)
- Approvers: [Arnav Goel](https://github.com/arnav-goel10)

Only files the release workflow builds from this repository, on GitHub's own
runners, are signed, and each signing request is approved by hand. The
Python and Qt libraries the app ships are their publishers' own files.
Privacy policy: see [Privacy](#privacy).
```

And a line for `installer/release_notes.md`: "Windows downloads are signed
through SignPath.io with a certificate by SignPath Foundation. See the
[code signing policy](https://github.com/arnav-goel10/mouse-double-click-fixer#code-signing-policy)."

### What signing doesn't cover

- **Upstream libraries.** The installed folder ships Python's and Qt's DLLs
  and extension modules as their publishers built them. The run's final
  check lists which carry a signature. SignPath's terms forbid signing them
  ourselves.
- **The uninstaller.** Inno Setup writes `unins000.exe` at install time.
  It is signed only when the installer is compiled with
  `SignedUninstaller=yes` and a sign tool Inno Setup can call itself. A
  remote signing service like SignPath doesn't fit that, so the uninstaller
  stays unsigned.
- **SmartScreen.** A signature lets reputation build up for the
  certificate, not separately for each file. It doesn't by itself stop the
  first-run warning.
- **macOS.** Unchanged. The app stays signed with its own certificate
  (see [Releasing](RELEASING.md#macos-signing-and-why-it-matters-for-updates)).

## 2. winget

winget installs from the manifests in
[microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs). A
package version is three YAML files in one pull request.

### The manifests

After a release is published:

```bash
python3 tools/winget_manifest.py 1.0.0
```

This downloads the release's `SHA256SUMS.txt` and its signature with `gh`.
It accepts them only if a release key signed them for 1.0.0, as installed
copies require. It writes
`build/winget/manifests/a/ArnavGoel/MouseDoubleClickFixer/1.0.0/` in the
repository's own layout and checks each file against winget's 1.12.0
schema (`tools/winget-schema/`). `--checksums FOLDER` reads a release
folder you already downloaded.

What the manifests say, and why:

- **`PackageIdentifier: ArnavGoel.MouseDoubleClickFixer`**, publisher
  "Arnav Goel", moniker `double-click-fixer`, tags for mouse, double-click,
  debounce and chatter, and `ReleaseNotesUrl` pointing at the release page.
- **The installer only, `InstallerType: inno`, `Scope: user`, x64.**
  winget runs an Inno Setup installer silently with its own switches,
  `/SP- /VERYSILENT /SUPPRESSMSGBOXES /NORESTART`. That is what CI's
  install test runs, over running copies, so the manifest names no
  switches.
- **`ProductCode` is Inno Setup's uninstall key**,
  `{6B0E2F4C-3D7A-4E51-9A0B-DC1F1C5E7A21}_is1`. Through it winget finds
  the installed copy and reads its version, so updates the app installs
  itself stay in step with winget.
- **`AppsAndFeaturesEntries`** records the uninstall entry's name ("Mouse
  Double-Click Fixer 1.0.0") and publisher ("Mouse Double-Click Fixer"),
  which differ from the package's name and publisher.
- **No portable entry.** The portable exe replaces itself when it updates,
  but winget tracks a portable package by the file it put down. The two
  would disagree.

### Submitting the first version

Check first, on a Windows PC:

```powershell
winget search --moniker double-click-fixer      # must find nothing: monikers are unique
winget validate --manifest build\winget\manifests\a\ArnavGoel\MouseDoubleClickFixer\1.0.0
winget settings --enable LocalManifestFiles     # once, as administrator
winget install --manifest build\winget\manifests\a\ArnavGoel\MouseDoubleClickFixer\1.0.0
```

Or test in Windows Sandbox with winget-pkgs' `Tools\SandboxTest.ps1`.

Then submit it one of two ways:

- **With wingetcreate** (`winget install wingetcreate`):

  ```powershell
  wingetcreate submit --prtitle "New package: ArnavGoel.MouseDoubleClickFixer version 1.0.0" build\winget\manifests\a\ArnavGoel\MouseDoubleClickFixer\1.0.0
  ```

  It asks you to sign in to GitHub, forks winget-pkgs for you and opens the
  pull request.
- **By hand:** fork microsoft/winget-pkgs, copy the `manifests/a/...`
  folder into the fork at the same path, and open a pull request with that
  one version in it.

The first pull request asks you to accept Microsoft's Contributor License
Agreement (the bot comments with the link). Validation then:

- checks the URLs;
- downloads the installer and checks its hash;
- scans it with several antivirus engines;
- installs it silently as a standard user;
- has a moderator review it.

If an engine flags the PyInstaller build (a known false-positive pattern),
submit the file to Microsoft at <https://www.microsoft.com/wdsi/filesubmission>
and say so on the pull request.

### Later versions

After each release is published, run the tool for that version and submit
the new folder the same way. Prefer this to `wingetcreate update`, which
starts from the previous version's manifest. It could keep the old version
in `AppsAndFeaturesEntries` and the old release date. One version per pull
request.

## 3. Microsoft Store

### The account

Individual developer accounts have been free since September 2025
([announcement](https://blogs.windows.com/windowsdeveloper/2025/09/10/free-developer-registration-for-individual-developers-on-microsoft-store/)).
Register at <https://storedeveloper.microsoft.com> with a personal Microsoft
account. Identity is checked with a government ID and a selfie. Then
reserve the name "Mouse Double-Click Fixer" in Partner Center.

### Route A: the existing installer (EXE)

[Store policy 10.2.9](https://learn.microsoft.com/en-us/windows/apps/publish/store-policies)
and the
[MSI/EXE package requirements](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msi/app-package-requirements):

| Requirement | Status |
| --- | --- |
| An `.exe` or `.msi` installer | `DoubleClickFixer-Setup.exe` |
| The installer **and all of its PE files** signed with a certificate from a CA in the Microsoft Trusted Root Program | **Blocked.** SignPath would sign the installer and our exe. The Python and Qt DLLs and `.pyd` files are as their publishers ship them, and some are unsigned; the release run's last check lists which. The uninstaller is unsigned (see above) |
| A versioned HTTPS URL whose file never changes | `https://github.com/arnav-goel10/mouse-double-click-fixer/releases/download/v1.0.0/DoubleClickFixer-Setup.exe` |
| Silent install with no UI (UAC is allowed) | `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART`, as CI's install test runs it. Per-user, no UAC |
| A standalone installer, not a downloader | Yes |
| PC only | Yes |
| A privacy policy URL (required for every Win32 app, policy 10.5.1) | `https://github.com/arnav-goel10/mouse-double-click-fixer#privacy` |

Until every PE file is signed, this route is closed. Opening it means either
upstream packages that ship signed binaries, or signing the libraries with a
certificate whose terms allow it (SignPath Foundation's don't).

When it opens, Partner Center asks for each package:

- **Package URL:** the versioned URL above, new for each release.
- **Architecture:** x64.
- **Language:** en-us.
- **App type:** EXE.
- **Installer parameters:** `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART`.
- **Return codes** (Inno Setup's
  [exit codes](https://jrsoftware.org/ishelp/topic_setupexitcodes.htm)):
  - 0: installation successful.
  - 2 and 5: installation cancelled by user.
  - 8: reboot required.
  - 1, 3, 4, 6 and 7: miscellaneous, with
    `https://github.com/arnav-goel10/mouse-double-click-fixer/blob/main/docs/TROUBLESHOOTING.md`
    as the documentation link.

The listing needs:

- a description (the README's opening works);
- screenshots (`docs/images/windows.png` and others at the Store's sizes);
- the category Utilities & tools;
- the age-rating questionnaire;
- Notes for certification, such as: "A tray utility. After install it starts
  in the notification area; open it from the Start menu. It filters mouse
  switch bounce with a low-level mouse hook (SetWindowsHookEx, WH_MOUSE_LL),
  reads no keyboard input, and needs no account or network access to work."

Copies installed from the Store update themselves through the app's own
updater, like any installed copy. Each new release also needs a Partner
Center submission with its new URL.

### Route B: MSIX

The Store signs MSIX packages itself, free, which removes the signing
blocker. It takes work in the app:

- package the installed folder as a full-trust desktop app;
- turn the self-updater off in that build, since the Store updates MSIX
  packages;
- move start at login to an MSIX startup task;
- allow for MSIX redirecting the app's per-user data, so settings from an
  existing install wouldn't carry over.

The low-level mouse hook should work in a full-trust package, but that is
unproven here. This is a separate project, not a release step.

## Sources

Checked on 2026-10-10.

- SignPath Foundation terms: <https://signpath.org/terms>; application:
  <https://signpath.org/apply>
- SignPath GitHub action, `v3.0` = commit
  `f6d04783b4569d051e0c80105fe66e82819d0092` (2026-09-10), inputs in its
  `action.yml`: <https://github.com/SignPath/github-action-submit-signing-request>
- SignPath with GitHub (token permissions `actions: read` and
  `contents: read`, GitHub-hosted runners for open-source projects,
  artifacts uploaded with `actions/upload-artifact` v4 or later):
  <https://docs.signpath.io/trusted-build-systems/github>
- Artifact configurations (`zip-file`, `pe-file`, `pe-file-set`,
  parameters, metadata restrictions):
  <https://docs.signpath.io/artifact-configuration/reference>,
  <https://docs.signpath.io/artifact-configuration/syntax>,
  <https://docs.signpath.io/artifact-configuration/examples>
- winget manifest schema 1.12.0 and its documentation:
  <https://github.com/microsoft/winget-cli/tree/master/schemas/JSON/manifests/v1.12.0>,
  <https://github.com/microsoft/winget-pkgs/tree/master/doc/manifest/schema/1.12.0>
- winget's default Inno Setup switches:
  `src/AppInstallerCommonCore/Manifest/ManifestCommon.cpp` in
  <https://github.com/microsoft/winget-cli>
- winget-pkgs submission and validation:
  <https://github.com/microsoft/winget-pkgs/blob/master/doc/Authoring.md>,
  <https://github.com/microsoft/winget-pkgs/blob/master/doc/Validation.md>,
  <https://github.com/microsoft/winget-pkgs/blob/master/doc/FirstContribution.md>;
  wingetcreate: <https://github.com/microsoft/winget-create/blob/main/doc/submit.md>
- Microsoft Store: policies 7.20 (10.2.9, 10.5.1):
  <https://learn.microsoft.com/en-us/windows/apps/publish/store-policies>;
  MSI/EXE requirements:
  <https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msi/app-package-requirements>;
  package fields:
  <https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msi/upload-app-packages>
- Inno Setup: exit codes
  <https://jrsoftware.org/ishelp/topic_setupexitcodes.htm>; the version
  details' defaults, `VersionInfoProductName` and
  `VersionInfoProductTextVersion`, in the same help
