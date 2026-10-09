# Security policy

## What the app can see

DoubleClick Fixer puts a filter in the system's input path so it can drop
bounced clicks before other apps see them: an event tap on macOS, a low-level
mouse hook on Windows.

- **macOS:** the event tap asks for mouse button events and pointer movement,
  through one tap so they stay in order. It never asks for keystrokes. macOS
  allows such a tap only for apps the user has allowed under **Privacy &
  Security › Device Control and Data Access** (**Accessibility** on macOS 26
  and earlier).
- **Windows:** a low-level mouse hook receives every mouse event and nothing
  from the keyboard. The app acts on button events, and looks at pointer
  movement only while it is holding a release back or re-sending one. It
  needs no special permission and runs with your account's rights.

The app uses which button went up or down, when, and where the pointer was. It
stores settings, a count of the bounces it blocked, and a log of start-up, the
filter starting and stopping, failures and updates, never clicks. It sends no
telemetry. Its only network use is the update check to this repository's
GitHub releases and downloading an update from there; turning off **Check for
updates automatically** stops the background checks.

## How updates are verified

Releases from 1.0 on are signed with [minisign](https://jedisct1.github.io/minisign/)
(Ed25519). The secret keys stay on the maintainer's Mac and never go to GitHub
or CI: CI builds a release as a draft, and it is signed there and only then
published. The app has the public halves of two keys built in, a primary and
a backup, so a lost or retired primary can be replaced without stranding
installed copies.

Before it offers an update, the app downloads the release's `SHA256SUMS.txt`
and `SHA256SUMS.txt.minisig` and checks that:

- the signature verifies, with one of the built-in keys, over exactly those
  checksums;
- its trusted comment is `dcf <version>` for exactly this release's version,
  so a signed older release can't be passed off under a newer tag;
- that version is newer than the one running.

A release that fails is never downloaded, and General says why. The download
itself must then match the signed checksum. On macOS the unpacked app must
also carry a valid code signature, report the release's version, and have the
same designated requirement as the installed app: that is what macOS ties the
app's permission to, so an update signed by anyone else is refused. A release
can move the app to a new signing certificate only by naming the new
requirement in its signed comment (`dcf <version> dr=<requirement>`), and the
new requirement must be certificate-based.

Copies older than 1.0 don't check signatures. They verify the update that
brings them to 1.0 against the release's checksums alone, which come from the
same release as the files. From 1.0 on, every update is signature-checked.

To check a download by hand, with minisign installed:

```bash
minisign -V -m SHA256SUMS.txt -P RWR9XcCRL9bZ1OD22V5J6uVhJgblDA9o4UFwJBjMU6CtTyqCeWOC6jDf
```

It must print `Trusted comment: dcf <version>`. Then compare the file's
SHA-256 (`shasum -a 256 <file>` on macOS, `Get-FileHash <file>` in
PowerShell) with its line in `SHA256SUMS.txt`. The backup key is
`RWTUZv3t2LmICpA6R0C6kmRCHkbU8DUvmw5kDkjIXZ1yJnUNuTXlEq7Z`.

## macOS hardening

The app is signed with the hardened runtime, so macOS ignores
`DYLD_INSERT_LIBRARIES` and similar variables and no other program can load
its code into the app that way and act with its permission. Its one
entitlement, `disable-library-validation`, is needed because its self-signed
certificate has no Team ID, which library validation would otherwise require
of the bundled Python and Qt. With that entitlement, a library inside the app
could still load code named in an environment variable (OpenSSL's
configuration, Qt's plugin paths and others), so the app clears those
variables before any of its code runs.

## Windows

The Windows builds are not signed with a code-signing certificate yet, so
SmartScreen warns about an unknown publisher, and Smart App Control, where it
is on, blocks them.
Updates don't depend on that: the updater checks the minisign signature above.
The installer installs for the current user and needs no administrator rights.
Windows doesn't let the app send input to windows running as administrator, so
over those it never holds a release back.

## Known limitations

- The macOS app isn't notarized. The copy in Applications belongs to the user
  who installed it, so other software running as that user could change files
  inside it, and a changed copy would keep the app's permission. The hardened
  runtime and the cleared environment stop code being loaded from outside the
  app, not changes to the app itself. Notarization would close this.
- The macOS code-signing certificate is self-signed, so macOS can't vouch for
  the publisher, and the first launch needs **Open Anyway**.

## Supported versions

Security fixes go into the latest release. The app updates itself, so please
stay on it.

## Reporting a vulnerability

Please report vulnerabilities privately through
[GitHub's private vulnerability reporting](https://github.com/arnav-goel10/doubleclick-fixer/security/advisories/new),
not as a public issue. Include the platform, app version, steps to reproduce
and the impact. You can expect an acknowledgement within a few days.
