# Security policy

## What the app does with your input

DoubleClick Fixer installs a system-wide mouse hook (a low-level mouse hook on
Windows, an event tap on macOS) so it can drop bounced clicks before other apps
see them. It only listens to mouse button events, never keystrokes, and it
keeps nothing but settings and a count of blocked bounces on your computer.
Its only network access is the update check to this repository's GitHub
releases, which can be turned off.

Updates are verified against the SHA-256 checksums published with each
release, and on macOS the downloaded app must carry the same code signature as
the installed one.

## Supported versions

Security fixes go into the latest release. The app updates itself, so please
stay on it.

## Reporting a vulnerability

Please report vulnerabilities privately through
[GitHub's private vulnerability reporting](https://github.com/arnav-goel10/doubleclick-fixer/security/advisories/new),
not as a public issue. Include the platform, app version, steps to reproduce
and the impact. You can expect an acknowledgement within a few days.
