# Security policy

## What the app can see

Mouse Double-Click Fixer puts a filter in the system's input path so it can
drop bounced clicks before other apps see them: an event tap on macOS, a
low-level mouse hook on Windows.

- **macOS:** the event tap asks for mouse button events and pointer movement,
  through one tap so they stay in order, and for scroll-wheel events only
  while the scroll-wheel fix is on. It never asks for keystrokes. macOS
  allows such a tap only for apps the user has allowed under **Privacy &
  Security › Device Control and Data Access** (**Accessibility** on macOS 26
  and earlier).
- **Windows:** a low-level mouse hook is given every mouse event and nothing
  from the keyboard. The app acts on button events, looks at wheel notches
  only while the scroll-wheel fix is on, and looks at pointer movement only
  while it is holding a release back or re-sending input. To learn which
  device a click or wheel notch came from, it also takes Raw Input from mice,
  precision touchpads and touchscreens, never keyboards. Windows hands the app
  every report from those devices, pointer movement included, whether or not
  the app is in front. The app notes only which device sent each report, when,
  and for a mouse which buttons or wheel it names. It uses no positions or
  finger contacts. It needs no special permission and runs with your
  account's rights.

The app uses which button went up or down, when, and where the pointer was.
To apply the Apps list it reads which app is in front (its bundle identifier on
macOS, its program's file name on Windows), and the apps that are running when
you open the add menu. To tell your pointing devices apart it reads each one's
name, vendor and product IDs and serial number, on macOS through IOKit.

It stores settings, which include the apps you listed and the devices you
chose not to filter; a count of the bounces it blocked; a history of daily
counts, in `wear.json`, never the time or place of a click: for each button
the presses, bounces, repaired dropouts, how long after a release each bounce
came and the filter window in use, and for the scroll wheel the notches judged
and reversals dropped; and a log of start-up, the filter starting and
stopping, permission changes, waking from sleep and switching users, the TLS
library in use, failures and updates, never clicks. It sends no telemetry. Its
only network use is the update check to this repository's GitHub releases and
downloading an update from there; turning off **Check for updates
automatically** stops the background checks.

## How updates are verified

Full releases from 1.0 on are signed with
[minisign](https://jedisct1.github.io/minisign/) (Ed25519). Pre-releases, which
are for testing, are never signed, and installed copies look only at the
latest full release, so they never offer one. CI builds each release and puts
it up as a draft. On the maintainer's Mac, a script then checks that every
file on the draft is byte for byte the one CI built, checks the macOS app's
code signature and that it loads no code named in its environment, signs the
checksums and publishes the release. The secret keys never leave that Mac. The
app has the public halves of two keys built in, a primary and a backup, so a
lost or retired primary can be replaced without stranding installed copies.

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

Copies older than 1.0 don't check minisign signatures. They verify the update
that brings them to 1.0 against the release's checksums alone (on macOS, plus
the code-signature check), and those come from the same release as the files.
From 1.0 on, every update is signature-checked.

Update checks and downloads go to GitHub over HTTPS. On Windows they use
Windows' own TLS (Schannel), and the Windows build ships no OpenSSL for Qt.
On macOS they use the OpenSSL that ships inside the app, the copy its Python
uses too, and the build's self-test fails if Qt loads an OpenSSL from outside
the app (macOS's own system libraries aside). If Qt's OpenSSL backend can't
start, the app uses macOS's own Secure Transport instead. Either way,
GitHub's certificate is checked against the certificates the system trusts. If
the app has no TLS library it may use, the update check fails and says so.
Updates don't rely on the connection, though: whatever it delivers is
installed only if the signature and checksums above verify.

The OpenSSL the app ships is Python's: OpenSSL 3.5, a long-term support
release. A build's self-test fails if any OpenSSL inside the app is older
(`OPENSSL_FLOOR` in `app/selftest.py`).

To check a download by hand, with minisign installed:

```bash
minisign -V -m SHA256SUMS.txt -P RWR9XcCRL9bZ1OD22V5J6uVhJgblDA9o4UFwJBjMU6CtTyqCeWOC6jDf
```

It must print `Trusted comment: dcf <version>`. Then compare the file's
SHA-256 (`shasum -a 256 <file>` on macOS, `Get-FileHash <file>` in
PowerShell) with its line in `SHA256SUMS.txt`. The backup key is
`RWTUZv3t2LmICpA6R0C6kmRCHkbU8DUvmw5kDkjIXZ1yJnUNuTXlEq7Z`.

## macOS hardening

The app is signed with the hardened runtime, so, with System Integrity
Protection on, macOS ignores `DYLD_INSERT_LIBRARIES` and similar variables and
no other program can load its code into the app that way and act with its
permission. Its one entitlement, `disable-library-validation`, is needed
because its self-signed certificate has no Team ID, which library validation
would otherwise require of the bundled Python and Qt. With that entitlement, a
library inside the app could still load code named in an environment variable
(OpenSSL's configuration, Qt's plugin paths and others). So before any of its
code runs, the app removes those variables and sets `OPENSSL_CONF` to
`/dev/null`, an empty configuration, rather than let OpenSSL read the file it
was built to look for, which can sit in a folder other software can write to.

The programs the app starts run as the app too: `pgrep`, `codesign`, `ditto`,
`open`, `hdiutil` (to eject the disk image it was installed from), and `bash`
for the update swap. It starts each by its full path, sets `PATH` to the system
folders, and runs `bash` with `-p` and without `BASH_ENV`, `ENV` or exported
functions, so nothing placed earlier on the `PATH` it was started with, and no
start-up script, runs in their place.

Before a release is published, `tools/macos_injection_check.sh` runs the app's
self-test with canary libraries and programs named in its environment
(`DYLD_INSERT_LIBRARIES`, `OPENSSL_CONF`, `PATH`, `BASH_ENV` and others), and
from a folder holding a canary under each bare name Qt's OpenSSL backend asks
the loader for. It fails if any canary loads or runs. The
`DYLD_INSERT_LIBRARIES` leg and the current-folder leg mean something only
with System Integrity Protection on: with it off, as on GitHub's Macs, the
loader honours the variable in any app and need not refuse a library by
relative path. So the build there skips those two legs and says so, and the
publishing Mac runs every leg and refuses to publish unless SIP is on.

## Windows

The Windows builds are not signed with a code-signing certificate yet, so
SmartScreen warns about an unknown publisher, and Smart App Control, where it
is on, blocks them.
Updates don't depend on that: the updater checks the minisign signature above.
It starts `cmd.exe`, and the Windows programs its update scripts run
(`tasklist`, `find` and `ping`), from System32 by full path: Windows looks
for a program named alone in the starting program's folder and the current
folder first, and the portable exe may sit in Downloads.
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
[GitHub's private vulnerability reporting](https://github.com/arnav-goel10/mouse-double-click-fixer/security/advisories/new),
not as a public issue. Include the platform, app version, steps to reproduce
and the impact. You can expect an acknowledgement within a few days.
