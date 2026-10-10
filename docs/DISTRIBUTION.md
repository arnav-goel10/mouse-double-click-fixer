# Distribution: Windows code signing, winget and the Microsoft Store

What the repository already does for each channel, and the steps only the
owner can take. Nothing here creates an account or submits anything; each of
those steps is yours.

## What blocks what

| Channel | Ready in the repository | Blocked by | Your steps |
| --- | --- | --- | --- |
| Signed Windows downloads | `release.yml` signs through SignPath once it is set up; until then it builds unsigned, as before | SignPath Foundation accepting the project. For programs it requires a verifiable reputation, which this project doesn't have yet, so acceptance may wait on that ([Eligibility](#eligibility-against-the-terms)) | [Apply](#the-application), then [set it up](#after-acceptance) |
| winget | `tools/winget_manifest.py` writes and checks the manifests | Nothing: a published release is enough. Signing isn't required | [Submit](#2-winget) the first version, then each new one |
| Microsoft Store, existing installer (EXE) | The installer installs silently, from a versioned URL. The Python, Qt and Microsoft libraries inside are already signed by their publishers | Every program file must be signed by a trusted CA: our exe and the installer (SignPath), and Inno Setup's uninstaller, which would take a third signing request per release. So this waits on SignPath's acceptance too | [See the checklist](#route-a-the-existing-installer-exe) |
| Microsoft Store, MSIX | Nothing yet | Packaging work in the app (no blocker outside it) | [See route B](#route-b-msix) |

A sensible order: submit 1.0.0 to winget once it is published. winget
needs no signature, and it checks the installer and its URLs, not how well
known the project is. Apply to SignPath once the project can show that
people other than you use it: reputation is the condition most likely to
stop the application, and SignPath asks applicants not to argue its
decisions ([Eligibility](#eligibility-against-the-terms)). The Store's EXE
route waits on SignPath; its MSIX route waits only on work in the app.

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
4. runs the portable exe's `--self-test`, signed or not, so a portable
   build that can't start (and so could never update itself) never ships.
   The installed exe's self-test runs in the install test, from the
   installer;
5. compiles the installer around the signed executable. SignPath can't sign
   files inside an Inno Setup installer, so the executables go first;
6. uploads the installer as `unsigned-windows-installer`. SignPath signs it
   under `windows-installer`, and the job puts it back;
7. checks every build, signed or not, for the product name and version
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
no draft goes up. Approving a request after its wait has ended does
nothing: the job that would have collected the signed file has already
failed. Re-run the failed jobs instead. The re-run uploads the two
`unsigned-*` artifacts again over the first run's (`overwrite: true`),
submits two new requests, and you approve each within its 30 minutes.
SignPath evaluates build policies (GitHub-hosted runners only, say) for at
most 3 re-runs of a build, and later re-runs fail while any such policy is
active; a policy set to `disallow_reruns` refuses re-runs altogether
([SignPath with GitHub](https://docs.signpath.io/trusted-build-systems/github)).
Past either, delete the tag and push it again, which starts a new run rather
than a re-run.

`tools/signpath/windows-executables.xml` and
`tools/signpath/windows-installer.xml` are the two artifact configurations.
`tests/test_release.py` checks that they match what the job uploads.

Only a signed run can prove the following. Check them on the first one (see
step 4 of [After acceptance](#after-acceptance)):

- **The installer's padded version details.** Inno Setup writes the
  installer's product name and version into fixed-size fields padded with
  spaces ("Mouse Double-Click Fixer" followed by spaces). The workflow's
  check compares them trimmed. If SignPath compares the raw text, it will
  refuse the installer. Ask SignPath how its restrictions treat trailing
  spaces before the first signed release.
- **Which version SignPath compares.** Each file's text version is `1.0.0`;
  its numeric version is 1.0.0.0. The configurations pass `1.0.0`.
- **SignPath fetching the artifacts.** It reads them with the job's token,
  which has `actions: read`.

The signed executables themselves need no check by hand: the run starts the
signed portable exe's self-test, and its install test runs the signed
installer and the installed exe's self-test.

### Eligibility, against the [terms](https://signpath.org/terms)

| Condition | This project |
| --- | --- |
| **Reputation.** Under "Common misunderstandings" the terms say SignPath won't sign binaries built from code nobody knows, and that for programs people download and run on the strength of its signature, "we require a certain verifiable reputation" (libraries are exempt) | **Not yet, and the condition most likely to stop the application.** The repository was created on 2026-09-17. On 2026-10-10 it had 0 stars, 0 forks, no issues, and pull requests only from you and Dependabot. Its release files showed 187 downloads in all, but 152 of them were of the 0.2.6 and 0.5.3 installers, which CI's install tests fetched on every run until they were cached (`tools/old_installers.py`). The first run on each branch or tag still fetches them, because Actions caches are scoped to a branch and `main` had no run that saved the cache yet. The rest look like one installed copy updating itself (a macOS update archive and a `SHA256SUMS.txt` per release). Nothing yet shows use by anyone else |
| **OSS license.** The terms: the project "must use an OSI-approved Open Source license without commercial dual-licensing for all components." | MIT. The app bundles Qt through PySide6 (LGPL-3.0), which The Qt Company also sells under a commercial licence. The sentence says "for all components" and doesn't say whether a bundled library's dual licence counts, so it could be read against the project; mention it in the application. SignPath's [project list](https://signpath.org/projects) includes Qt applications, Flameshot and Stellarium among them |
| No malware or potentially unwanted programs | None. The app filters mouse input and never invents a click |
| No proprietary components, though System Libraries (as section 1 of the GPL v3 defines them) may be included | Python, Qt/PySide6 and the other bundled packages are open source. The exception is Microsoft's Visual C++ runtime (`VCRUNTIME140.dll`, `VCRUNTIME140_1.dll` and the `MSVCP140*.dll` files), the runtime of the compiler Python and Qt are built with. It is proprietary, shipped unmodified as Microsoft's Distributable Code, and `THIRD_PARTY_NOTICES-windows.md` says so. Python's two copies are signed by Microsoft, and the copies inside PySide6 and shiboken6 by The Qt Company. It fits GPL v3 section 1's System Libraries, which come with a Major Component, and a Major Component includes "a compiler used to produce the work". Name it in the application |
| Actively maintained, already released in the form to be signed | Releases since 0.2.0 (2026-09-19). Each ships `DoubleClickFixer-Setup.exe` and `DoubleClickFixer.exe` |
| Functionality described on the download page | The README and every release page |
| The signing team is the development team and owns the repository | One maintainer: Arnav Goel |
| Multi-factor authentication for GitHub and SignPath | Yes for GitHub; turn it on for SignPath when the account exists |
| Binaries built verifiably from source | `release.yml` on GitHub-hosted runners, from the tag. Every Python package is pinned by hash (`requirements-build.txt`), and Inno Setup is pinned to 6.7.1 |
| Manual approval of every release | Two requests per release, both approved by you |
| Product name and version enforced by metadata restrictions | Both artifact configurations require product name "Mouse Double-Click Fixer" and the release's version |
| Only your own code signed | Only the three files built here. The Python and Qt libraries beside the installed exe are never sent to SignPath |
| Privacy: software that collects user data and transfers it to systems the user didn't name must describe this in a privacy policy, show that policy during installation, and offer an option at install time to turn it off | No telemetry, and nothing about the user is collected. What the app learns on the computer stays there: the executable name of the app in front (kept in memory, to compare with the user's list of excluded apps), the executable names of the apps the user adds to that list and the keys of the mice the user chooses to ignore (both in `settings.json`), the daily counts in `wear.json`, and which device sent each Raw Input report and when (kept in memory; no position is used). The only network use is the update check and download from the project's GitHub Releases. The check is on by default; it asks GitHub for the latest release, with the app's version in its User-Agent and nothing else beyond what any web request carries (the IP address). **General › Check for updates automatically** turns it off (README, [Privacy](../README.md#privacy)). Be ready to argue that this isn't user data. If SignPath disagrees, the installer must show the privacy policy and offer a checkbox that turns the automatic check off; that isn't built |
| System changes announced; an uninstaller | Start at login is off unless chosen. The installer registers an uninstaller; the portable exe is one file to delete |
| A "Code signing policy" on the home page | **To do once accepted:** add [the section below](#the-code-signing-policy-section) to the README and the release page |

SignPath Foundation decides on each application and needn't accept any.
The terms put no number on reputation, and SignPath says it generally
doesn't discuss its policy, so there is no threshold to aim for. What can
show it:

- people other than you using the app and saying so: issues, discussions,
  stars and forks on the repository;
- posts, reviews or forum answers elsewhere that recommend it;
- a winget listing, which Microsoft's moderators reviewed and scanned
  ([winget](#2-winget));
- download counts, once CI's own are taken out. CI's install tests
  downloaded the 0.2.6 and 0.5.3 installers from their release pages on every
  run until they were cached, and the first run on each branch or tag downloads them
  again until `main` has a run that saved the cache, so those two releases' counts
  overstate use, and always will.

Count again before applying:

```bash
repo=arnav-goel10/mouse-double-click-fixer
gh api repos/$repo --jq '{created_at, stargazers_count, forks_count, open_issues_count}'
gh api repos/$repo/releases --paginate \
  --jq '.[] | select(.draft | not) | .tag_name as $tag | .assets[] | "\($tag) \(.name) \(.download_count)"'
```

### The application

Apply at <https://signpath.org/apply>. The form loads from HubSpot and
couldn't be read in advance, so the text below covers what applications
usually ask for. Paste the parts the form wants. Fill in **Use so far** when
you apply, with links to what the list under
[Eligibility](#eligibility-against-the-terms) names; it answers the
condition most likely to stop the application.

> **Project:** Mouse Double-Click Fixer
>
> **Repository:** https://github.com/arnav-goel10/mouse-double-click-fixer
> (public; the only repository; issues and releases on GitHub)
>
> **Licence:** MIT (`LICENSE` in the repository). Bundled third-party
> components are open source: CPython (PSF), Qt 6 via PySide6 (LGPL-3.0),
> and others listed in `THIRD_PARTY_NOTICES-windows.md` on every release.
> The one exception is Microsoft's Visual C++ runtime (`VCRUNTIME140*.dll`,
> `MSVCP140*.dll`), the runtime of the compiler Python and Qt are built
> with, shipped unmodified as Microsoft's redistributable and signed by
> Microsoft or The Qt Company.
>
> **Downloads:** https://github.com/arnav-goel10/mouse-double-click-fixer/releases
> (free; no account, no payment)
>
> **Use so far:** (fill in when applying: links that show people other than
> the maintainer using it, such as their issues or discussions, posts that
> recommend it, and the winget listing once merged,
> `winget install ArnavGoel.MouseDoubleClickFixer`. Leave out release
> download counts unless CI's own downloads are taken out.)
>
> **What it does:** a tray app for Windows 10 1809+/11 and macOS 13+ that
> fixes mice whose worn switches send two clicks for one ("switch bounce"
> or "chatter"). On Windows it:
>
> - filters the extra clicks with a low-level mouse hook (`SetWindowsHookEx`,
>   `WH_MOUSE_LL`) and leaves real double-clicks alone. The left button is
>   filtered by default; the right, middle and side buttons (back and
>   forward) are the user's choice;
> - optionally (off by default) drops a scroll-wheel tick that reverses
>   direction within a short window (50 ms by default), the stray notch of a
>   worn wheel encoder, vertical and horizontal ticks each judged on their
>   own. The same hook sees the ticks; nothing is held back or re-sent. A
>   tick is judged only if a mouse's Raw Input report carries a wheel notch
>   for that axis, so a precision touchpad's scrolling, which no mouse
>   reports, is left alone;
> - tells which device a click or wheel tick came from with Raw Input: a
>   hidden window of its own registers (`RegisterRawInputDevices`,
>   `RIDEV_INPUTSINK`, `RIDEV_DEVNOTIFY`) for mice, precision touchpads and
>   touchscreens, never keyboards. Windows delivers every report from them to
>   that window, pointer movement included, whether or not the app is in
>   front. The app notes only which device sent each report, when, and for a
>   mouse which buttons or wheel it names; it uses no positions.
>   `RIDEV_DEVNOTIFY` tells it when a device is connected or removed. Clicks
>   from precision touchpads, touchscreens and pens pass untouched, except
>   that a touchpad tap within a second of any mouse's report is taken for
>   that mouse's click, and the user can put any mouse on a per-device ignore
>   list;
> - watches which app is in front (`SetWinEventHook`,
>   `EVENT_SYSTEM_FOREGROUND`), so clicks pass untouched while an app on the
>   user's exclusion list (a game, say) is in front. It reads the executable
>   name of the foreground window's process and compares it with that list;
>   the name is kept in memory only. The picker for that list shows the
>   programs that have a window open;
> - keeps a local wear history, `wear.json` beside the settings: for each
>   button and day, the presses it saw, the bounces it dropped and a
>   histogram of the bounces' gaps, plus the wheel ticks seen and
>   reversals dropped. It records no time or place of a click, and the file
>   is never sent anywhere.
>
> It never invents a click. To keep events in order it re-sends, with
> `SendInput`, a release it held back for the filter window and the input
> that came meanwhile; apps see these as injected input (README,
> "Will it get me flagged in games?"). No telemetry. Its only network use is
> the update check and download from the project's GitHub Releases, verified
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
>
> **One question:** Inno Setup writes the installer's product name and
> version into fixed-size version fields, padded with trailing spaces
> ("Mouse Double-Click Fixer" followed by spaces). Do the `product-name`
> and `product-version` restrictions compare these values trimmed, and
> against the string version or the numeric one? If not trimmed, how should
> the installer's artifact configuration be written?

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
   - If the application's question about the installer's padded version
     details is still unanswered, get the answer now and change
     `tools/signpath/windows-installer.xml` to match before the first
     signed tag (see [How the release workflow signs](#how-the-release-workflow-signs)).
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
4. **Try it on a pre-release.** A release tag must carry the version in
   `app/__init__.py` (`tests/test_release.py` checks it on every tag), and
   the changelog needs a heading for that version, which for a pre-release
   needs no date. So the simplest trial is a pre-release of a version not
   yet released. If 1.0.0 isn't tagged yet, tag `v1.0.0-rc.1` at the commit
   you'd release. Once 1.0.0 is out, first commit the next version, such
   as `__version__ = "1.0.1"` and a `## 1.0.1` changelog entry, then tag
   `v1.0.1-rc.1`. Approve the two requests in SignPath as the run reaches
   them, and check the Windows job's "Check what SignPath checks, and every
   signature" step: all three files `Valid`, signed by SignPath Foundation.
   Then delete the draft and the tag. The first real signed release is the
   next tag after that.

**To stop signing,** delete the token and all three variables, and revoke
the CI user's token in SignPath. The next tag's build then logs "Not
signed" and goes out unsigned. Deleting only some of them makes the next
tag's Windows build fail instead, naming what is missing, because a
half-finished setup is more likely a mistake than a choice:

```bash
repo=arnav-goel10/mouse-double-click-fixer
gh secret delete SIGNPATH_API_TOKEN --env release --repo $repo
gh variable delete SIGNPATH_ORGANIZATION_ID --env release --repo $repo
gh variable delete SIGNPATH_PROJECT_SLUG --env release --repo $repo
gh variable delete SIGNPATH_POLICY_SLUG --env release --repo $repo
```

### The code signing policy section

The terms require it on the home page, under that name, and on download or
release pages. For the README, below **Privacy**:

```markdown
## Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io),
certificate by [SignPath Foundation](https://signpath.org).

- Committers and reviewers: [Arnav Goel](https://github.com/arnav-goel10)
- Approvers: [Arnav Goel](https://github.com/arnav-goel10)

This covers the Windows downloads. Only files the release workflow builds
from this repository, on GitHub's own runners, are signed, and each signing
request is approved by hand. The Python and Qt libraries the app ships are
their publishers' own files. Privacy policy: see [Privacy](#privacy).
```

The first sentence is the terms' wording; keep it exactly. And a line for
`installer/release_notes.md`: "Windows downloads: free code signing
provided by SignPath.io, certificate by SignPath Foundation. See the
[Code signing policy](https://github.com/arnav-goel10/mouse-double-click-fixer#code-signing-policy)."

### What signing doesn't cover

- **Upstream libraries.** The installed folder ships Python's, Qt's and
  Microsoft's DLLs and extension modules as their publishers built them,
  and SignPath's terms forbid signing them ourselves. They don't need it.
  The workflow's last check lists every program file in the installed
  folder with its signer. A build of 2026-10-09 listed 49 of the 50 as
  validly signed: 18 by the Python Software Foundation, 29 by The Qt Company
  (its copies of the Visual C++ runtime among them) and 2 by Microsoft
  (Python's copies of that runtime). The 50th is `DoubleClickFixer.exe`,
  which SignPath signs.
- **The uninstaller.** Inno Setup writes `unins000.exe` at install time,
  unsigned unless the installer is compiled with `SignedUninstaller=yes`
  ([help](https://jrsoftware.org/ishelp/topic_setup_signeduninstaller.htm)).
  The compiler then either signs it on the fly with a `SignTool` it runs
  itself, or embeds the signature of a copy signed beforehand and kept in
  `SignedUninstallerDir`. That copy only fits while the uninstaller's
  contents stay the same, and they change with the `VersionInfo`
  directives: `installer/windows.iss` sets `VersionInfoVersion` to each
  release's version, so every release needs a newly signed copy. Either way
  that is a third signing request per release (for the copy: compile once
  to write it, have SignPath sign it, compile again), and a third approval.
  Neither is built.
- **SmartScreen.** A signature doesn't by itself stop the first-run
  warning. Microsoft's
  [SmartScreen reputation for Windows app developers](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
  says SmartScreen weighs two things, the reputation of the file's own hash
  and that of the publisher's signing certificate, and that a signed file
  can still be flagged as unrecognized until one of them has built up. It
  gives no threshold, only that this can take several weeks and hundreds of
  clean installs from a wide audience. What signing buys is that the
  certificate's reputation can carry over to later versions signed with it;
  an unsigned file starts at zero with every version. The publisher shown is
  SignPath Foundation.
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
  `/SP- /VERYSILENT /SUPPRESSMSGBOXES /NORESTART`, so the manifest names
  none. CI's install test (`tools/windows_install_e2e.ps1`) runs
  `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=...` over running copies:
  the same, less `/SP-` (which skips Inno Setup's opening "This will
  install..." prompt, [help](https://jrsoftware.org/ishelp/topic_setupcmdline.htm))
  and plus a log file. So CI doesn't run winget's exact command line;
  `winget install --manifest` under "Submitting the first version" does.
- **`ProductCode` is Inno Setup's uninstall key**,
  `{6B0E2F4C-3D7A-4E51-9A0B-DC1F1C5E7A21}_is1`. Through it winget finds
  the installed copy and reads its version, so updates the app installs
  itself stay in step with winget.
- **`AppsAndFeaturesEntries`** records the uninstall entry's name ("Mouse
  Double-Click Fixer 1.0.0") and publisher ("Mouse Double-Click Fixer"),
  which differ from the package's name and publisher. winget-pkgs'
  [Authoring guide](https://github.com/microsoft/winget-pkgs/blob/master/doc/Authoring.md)
  (Testing) asks for the entry when they differ. The version isn't a
  reason: Inno Setup writes a `DisplayVersion` equal to `PackageVersion`,
  which the same guide's "When is AppsAndFeaturesEntries needed?" says
  needs no entry. If a moderator asks to drop it, drop it; the
  `ProductCode` still ties the installed copy to the package.
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

- checks the URLs: reachable, HTTPS, and not flagged by SmartScreen;
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
| The installer **and all of its PE files** signed with a certificate from a CA in the Microsoft Trusted Root Program | **Blocked.** SignPath would sign our exe and the installer, but the uninstaller stays unsigned. The libraries are already signed by their publishers (49 of 50 files; see [What signing doesn't cover](#what-signing-doesnt-cover)) |
| A versioned HTTPS URL whose file never changes | `https://github.com/arnav-goel10/mouse-double-click-fixer/releases/download/v1.0.0/DoubleClickFixer-Setup.exe` |
| Silent install with no UI (UAC is allowed) | `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART`, which CI's install test runs (with a `/LOG=` added). Per-user, no UAC |
| A standalone installer, not a downloader | Yes |
| PC only | Yes |
| A privacy policy URL. Policy 10.5.1 requires one when a product accesses, collects or transmits personal information, and always for Win32 products, which it counts among those that "inherently have access to Personal Information" | `https://github.com/arnav-goel10/mouse-double-click-fixer#privacy` |

This route opens once SignPath signs releases and the uninstaller is
signed too. That needs a third signing request per release, for the
uninstaller (see [What signing doesn't cover](#what-signing-doesnt-cover)).
Alternatively, ask Microsoft whether an uninstaller written at install time
counts among "all of its PE files". Neither is built.

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
- Notes for certification, such as: "A tray utility. After install it
  starts in the notification area; open it from the Start menu. It filters
  mouse switch bounce with a low-level mouse hook (SetWindowsHookEx,
  WH_MOUSE_LL) on the left, right, middle and side buttons, and can drop a
  scroll-wheel tick that reverses within a short window (off by default;
  it judges only a tick that a mouse's Raw Input report carries). A button
  release it held back for that window, and the input that came meanwhile,
  are re-sent with SendInput, as injected input; it never makes a click of
  its own. To tell which device a click or wheel tick came from, a hidden
  window registers for Raw Input (RegisterRawInputDevices,
  RIDEV_INPUTSINK, RIDEV_DEVNOTIFY) from mice, precision touchpads and
  touchscreens only. It notes which device sent each report and reads no
  positions. Clicks from precision touchpads, touchscreens and pens pass
  untouched (a touchpad tap within a second of a mouse's report is taken
  for that mouse's click), and the user can ignore a chosen mouse. So that
  the user can exclude apps such as games, it watches which app is in
  front (SetWinEventHook, EVENT_SYSTEM_FOREGROUND) and compares that
  program's executable name with the user's list; the name is kept in
  memory only. It keeps daily counts of presses, bounces and wheel ticks
  in wear.json in its settings folder, which is never sent anywhere. It
  reads no keyboard input and needs no account or network access to work;
  its update check, which can be turned off, is its only network use."

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

- SignPath Foundation terms (the license condition is under "Conditions for
  free OSS SignPath.io subscriptions", the reputation condition under
  "Common misunderstandings"): <https://signpath.org/terms>; application:
  <https://signpath.org/apply>; projects it signs:
  <https://signpath.org/projects>
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
- SmartScreen: <https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation>
  (the page's own date is 2026-05-04), and its overview at
  <https://learn.microsoft.com/en-us/windows/security/operating-system-security/virus-and-threat-protection/microsoft-defender-smartscreen/>
- `actions/upload-artifact` `v7.0.2` = commit
  `cf430e030ddbb5b0abf93d22962f4752f3646cd9`, its `overwrite` input in
  `action.yml` and "Overwriting an Artifact" in its README:
  <https://github.com/actions/upload-artifact>
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
- Inno Setup: command line switches (`/SP-`)
  <https://jrsoftware.org/ishelp/topic_setupcmdline.htm>; exit codes
  <https://jrsoftware.org/ishelp/topic_setupexitcodes.htm>; signing the
  uninstaller, `SignedUninstaller`:
  <https://jrsoftware.org/ishelp/topic_setup_signeduninstaller.htm>; the version
  details' defaults, `VersionInfoProductName` and
  `VersionInfoProductTextVersion`, in the same help
