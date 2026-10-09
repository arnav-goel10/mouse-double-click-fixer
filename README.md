<div align="center">

<img src="docs/images/icon.png" width="128" height="128" alt="DoubleClick Fixer icon">

# DoubleClick Fixer

**Fix a mouse that double-clicks when you click once.**<br>
A free, open-source app for Windows and macOS that stops mouse double-clicking caused by a worn switch. It filters the extra clicks (switch bounce, or "chatter") and leaves your real double-clicks alone.

[![Latest release](https://img.shields.io/github/v/release/arnav-goel10/doubleclick-fixer?label=release&color=3055ee)](https://github.com/arnav-goel10/doubleclick-fixer/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/arnav-goel10/doubleclick-fixer/total?color=3055ee)](https://github.com/arnav-goel10/doubleclick-fixer/releases)
[![CI](https://github.com/arnav-goel10/doubleclick-fixer/actions/workflows/ci.yml/badge.svg)](https://github.com/arnav-goel10/doubleclick-fixer/actions/workflows/ci.yml)
[![Platforms](https://img.shields.io/badge/platform-macOS%2013%2B%20(Apple%20silicon)%20%7C%20Windows%2010%2F11%20(x64)-lightgrey)](#download)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**[Download for Mac](https://github.com/arnav-goel10/doubleclick-fixer/releases/latest/download/DoubleClickFixer.dmg)** &nbsp;·&nbsp;
**[Download for Windows](https://github.com/arnav-goel10/doubleclick-fixer/releases/latest/download/DoubleClickFixer-Setup.exe)** &nbsp;·&nbsp;
[All releases](https://github.com/arnav-goel10/doubleclick-fixer/releases)

</div>

<p align="center">
  <img src="docs/images/macos.png" width="49%" alt="DoubleClick Fixer on macOS: the Bounce Filter settings, with the filter on at 46 ms">
  <img src="docs/images/windows.png" width="49%" alt="DoubleClick Fixer on Windows 11: the Bounce Filter settings, with the filter on at 46 ms">
</p>
<p align="center"><sub>macOS &nbsp;·&nbsp; Windows 11</sub></p>

## The problem

Mouse buttons wear out. The metal contact inside starts to bounce, so a single click arrives twice. If your mouse does any of these, this is the app for it:

- it **double-clicks when you click once**: files open when you meant to select them, links open in two tabs;
- **drag and drop lets go** halfway, or text selection keeps restarting;
- a **held button seems to release on its own** while you drag a window or a file.

It happens to every brand sooner or later, Logitech, Razer, SteelSeries, Microsoft and others, and it's especially common on gaming mice. Replacing the switch fixes it for good. If your mouse's own software has a debounce setting, try raising that first; otherwise DoubleClick Fixer fixes it in software, on Windows 10, Windows 11 and macOS.

## How it works

A bounce has a tell-tale signature: the extra press arrives a few milliseconds after the button was released, far faster than a finger can lift and press again. A deliberate double-click leaves the button up for much longer.

- **Clicks.** A press that comes within your filter window (typically 25–60 ms) of the last release is dropped, along with its release, so apps never see half a click.
- **Drags.** A worn switch can also lose contact for an instant while you hold it, which would end a drag. So every release is held back until the filter window has passed. If the contact comes straight back, both are dropped and the drag simply continues; otherwise the release is delivered, where you let go of the button.
- **Clicks followed by a quick move.** When you click in place and move away, the release goes out as the pointer leaves the spot, before the movement reaches apps, so the click lands where you made it.
- **Your double-clicks are untouched.** Calibration measures your own double-click speed and keeps the filter well below it.

The filter sits in the system's own input path, an event tap on macOS and a low-level mouse hook on Windows, so every app sees the filtered clicks. Clicks are timed by the events' own timestamps on macOS and by a precise clock on Windows, so a busy computer doesn't distort the gaps. Events reach the filter in the order they happened, so a held release is settled by the time of the events that follow it: once one from after the filter window arrives, no press can still be on its way to cancel it. With nothing after it, as when you click and keep the mouse still, a timer settles it after the window plus an allowance for how late this computer delivers mouse events. On macOS, clicks and pointer movement come through one event tap, so a move can never reach apps ahead of a click's release. All of it happens on your computer.

## Features

- **System-wide filtering** for the left, right and middle buttons, each one optional.
- **Drag protection** from the moment the button goes down.
- **Calibration** that measures your mouse's bounce and your double-click speed, then recommends a filter window.
- **Test pane** that shows every click's release-to-press gap, for every button, with bounces highlighted.
- **Native on both platforms:** a System Settings-style window and menu bar item on macOS, a Windows 11 Settings-style window and notification area icon on Windows. Light and dark mode follow the system. Switches and the sidebar are exposed to screen readers such as VoiceOver and Narrator, and keyboard focus is shown.
- **Runs quietly:** lives in the menu bar or notification area, opens at login if you want, and has no Dock icon while its window is closed.
- **Signed automatic updates** from this repository's releases. Each release is signed on the maintainer's computer, with a key that never goes to GitHub or CI, and the app installs nothing that fails the check. Checking and installing can each be turned off.
- **Copy Diagnostics** puts the version, the filter's state and the app's recent log on the clipboard for a bug report.

## Download

| Platform | Get it | Notes |
| --- | --- | --- |
| **macOS 13 or later** (Apple silicon) | [DoubleClickFixer.dmg](https://github.com/arnav-goel10/doubleclick-fixer/releases/latest/download/DoubleClickFixer.dmg) | Open it and drag DoubleClick Fixer onto Applications. |
| **Windows 10** (version 1809 or later) **or 11**, 64-bit | [DoubleClickFixer-Setup.exe](https://github.com/arnav-goel10/doubleclick-fixer/releases/latest/download/DoubleClickFixer-Setup.exe) | Or the [portable exe](https://github.com/arnav-goel10/doubleclick-fixer/releases/latest/download/DoubleClickFixer.exe), no installation needed. |

The apps are not yet signed with an Apple or Microsoft developer certificate, so the first launch takes an extra step:

- **macOS 15 and later:** open the app; macOS says it can't verify it. Choose **Done**, go to **System Settings › Privacy & Security**, scroll to the message about DoubleClick Fixer, choose **Open Anyway** (it is there for about an hour after you tried) and confirm with your password. On macOS 13 and 14, Control-click the app in Applications and choose **Open** instead.
- **Permission on macOS:** turn on **Bounce Filter**, and allow DoubleClick Fixer when macOS asks, under **Privacy & Security › Device Control and Data Access** (**Accessibility** on macOS 26 and earlier). macOS only lets apps you approve filter input.
- **Windows:** if SmartScreen appears, choose **More info › Run anyway**. If Smart App Control blocks the app, Windows offers no way to run it anyway; see [Installation](docs/INSTALLATION.md#smart-app-control).

Full instructions, including uninstalling: [docs/INSTALLATION.md](docs/INSTALLATION.md).

## Getting started

1. Open DoubleClick Fixer and choose **Calibrate** in the sidebar.
2. Click the pad once at a time until it has counted twelve clicks, then double-click five times at your usual speed.
3. Apply the recommendation, and turn on **Bounce Filter**.

Close the window and the app keeps filtering from the menu bar or notification area. More detail in [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md), and help in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## FAQ

<details>
<summary><b>Will it block my real double-clicks?</b></summary>

Not once calibrated. Only a press within the filter window of the previous release is dropped, and calibration keeps that window at no more than half of your fastest measured double-click. Before you calibrate, the default 60 ms window can catch a very fast double-click, one that leaves the button up for less than 60 ms.
</details>

<details>
<summary><b>Does it add input lag?</b></summary>

Presses go straight through. The only press that waits is one that comes while a release is still on its way to apps: it goes out right after that release, usually within a millisecond or two, so apps see the two in order. Every release waits until the filter window (typically 25–60 ms) has passed, so a contact dropout can't end a drag. If you move the mouse or click again, it goes out as soon as the window is over. On a still mouse it waits longer: the window plus an allowance for how late your computer delivers mouse events, which the app measures as it runs and keeps between 5 and 150 ms. A click made in place is released sooner, as soon as you move the pointer off it.
</details>

<details>
<summary><b>Are drags protected from the start?</b></summary>

Yes. Every release is held, so a dropout is caught however soon after the press it comes, as long as the contact comes back within the filter window. A dropout longer than the window still ends the drag.
</details>

<details>
<summary><b>Why does it need permission on macOS?</b></summary>

Blocking a click before other apps see it requires an event tap, and macOS only allows that for apps you have approved, under **Privacy & Security › Device Control and Data Access** (**Accessibility** on macOS 26 and earlier). The app asks only for mouse events, never keystrokes.
</details>

<details>
<summary><b>Will it get me flagged in games?</b></summary>

The app never invents a click, but it does re-send some input: a release it held back, a press or pointer movement that waited behind it, and a move that puts the pointer back after a drag let go while moving. Apps receive these as software input (on Windows, marked as injected). Most games don't care, but some anti-cheat systems watch for software input; turn the filter off before playing those.
</details>

## Privacy

- The app receives mouse button events and pointer movement, and nothing from the keyboard. It uses which button went up or down, when, and where the pointer was, and keeps none of it beyond a count of the bounces it blocked.
- No telemetry, no analytics, no accounts.
- Its only network use is the update check to this repository's GitHub releases, and downloading an update from there. Turn off **General › Check for updates automatically** and it makes no request unless you ask it to check.
- Its log records start-up, the filter starting and stopping, failures and updates, never clicks. It stays on your computer unless you paste it into a bug report.

## Known limitations

- Side buttons (back and forward) and the scroll wheel are not filtered.
- The app is in English only.
- Some games' anti-cheat systems object to software input; turn the filter off before playing them. On Windows, games that read raw input receive the clicks and moves the app re-sends as injected input, not as coming from your mouse.
- On Windows, drag protection is off over windows of apps running as administrator, which Windows doesn't let an ordinary app send input to; see [Troubleshooting](docs/TROUBLESHOOTING.md#windows-apps-running-as-administrator).
- On macOS the app isn't notarized yet. The copy you drag into Applications belongs to your account, so other software running as you could change it, and a changed copy would keep the app's permission. That stays possible until the app is notarized.

## Documentation

- [Installation](docs/INSTALLATION.md), including uninstalling
- [Getting started](docs/GETTING_STARTED.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Security](SECURITY.md): what the app can see, and how updates are verified
- [Changelog](CHANGELOG.md)
- [Releasing](docs/RELEASING.md), for maintainers

## Build from source

Python 3.11 or newer.

```bash
git clone https://github.com/arnav-goel10/doubleclick-fixer
cd doubleclick-fixer
python3 -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

Run the tests with `python -m unittest discover -s tests`. Packaging and releases are covered in [docs/RELEASING.md](docs/RELEASING.md).

## Contributing

Bug reports and pull requests are welcome; start with [CONTRIBUTING.md](CONTRIBUTING.md). Please report security issues privately as described in [SECURITY.md](SECURITY.md).

## Support the project

DoubleClick Fixer is free. If it saved you from buying a new mouse, star the repository.

## Star history

[![Star History Chart](https://api.star-history.com/svg?repos=arnav-goel10/doubleclick-fixer&type=Date)](https://star-history.com/#arnav-goel10/doubleclick-fixer&Date)

## License

[MIT](LICENSE) © 2026 Arnav Goel

The apps are built on Qt and Qt for Python (LGPL-3.0), Python and other open-source software; their licences are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), and each build carries its own copy (**General › Acknowledgements…**).
