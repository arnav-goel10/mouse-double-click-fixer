<div align="center">

<img src="docs/images/icon.png" width="128" height="128" alt="Mouse Double-Click Fixer icon">

# Mouse Double-Click Fixer

**Fix a mouse that double-clicks when you click once.**<br>
A free, open-source app for Windows and macOS that stops mouse double-clicking caused by a worn switch. It filters the extra clicks (switch bounce, or "chatter") and leaves your real double-clicks alone.

[![Latest release](https://img.shields.io/github/v/release/arnav-goel10/mouse-double-click-fixer?label=release&color=3055ee)](https://github.com/arnav-goel10/mouse-double-click-fixer/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/arnav-goel10/mouse-double-click-fixer/total?color=3055ee)](https://github.com/arnav-goel10/mouse-double-click-fixer/releases)
[![CI](https://github.com/arnav-goel10/mouse-double-click-fixer/actions/workflows/ci.yml/badge.svg)](https://github.com/arnav-goel10/mouse-double-click-fixer/actions/workflows/ci.yml)
[![Platforms](https://img.shields.io/badge/platform-macOS%2013%2B%20(Apple%20silicon)%20%7C%20Windows%2010%2F11%20(x64)-lightgrey)](#download)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**[Download for Mac](https://github.com/arnav-goel10/mouse-double-click-fixer/releases/latest/download/DoubleClickFixer.dmg)** &nbsp;·&nbsp;
**[Download for Windows](https://github.com/arnav-goel10/mouse-double-click-fixer/releases/latest/download/DoubleClickFixer-Setup.exe)** &nbsp;·&nbsp;
[All releases](https://github.com/arnav-goel10/mouse-double-click-fixer/releases)

</div>

<p align="center">
  <img src="docs/images/macos.png" width="49%" alt="Mouse Double-Click Fixer on macOS: the Bounce Filter pane">
  <img src="docs/images/windows.png" width="49%" alt="Mouse Double-Click Fixer on Windows 11: the Bounce Filter pane">
</p>
<p align="center"><sub>macOS &nbsp;·&nbsp; Windows 11</sub></p>

## The problem

Mouse buttons wear out. The metal contact inside starts to bounce, so a single click arrives twice. If your mouse does any of these, this is the app for it:

- it **double-clicks when you click once**: files open when you meant to select them, links open in two tabs;
- **drag and drop lets go** halfway, or text selection keeps restarting;
- a **held button seems to release on its own** while you drag a window or a file.

It happens to every brand sooner or later, Logitech, Razer, SteelSeries, Microsoft and others, and it's especially common on gaming mice. Replacing the switch fixes it for good. If your mouse's own software has a debounce setting, try raising that first; otherwise Mouse Double-Click Fixer fixes it in software, on Windows 10, Windows 11 and macOS.

## How it works

A bounce has a tell-tale signature: the extra press arrives a few milliseconds after the button was released, far faster than a finger can lift and press again. A deliberate double-click leaves the button up for much longer.

- **Clicks.** A press that comes within the filter window of the last release is dropped, along with its release, so apps never see half a click. The window is 46 ms until you calibrate, and each button has its own.
- **Drags.** A worn switch can also lose contact for an instant while you hold it, which would end a drag. So every release of the left, right and middle buttons is held back until the filter window has passed. If the contact comes straight back, both are dropped and the drag simply continues; otherwise the release is delivered, where you let go of the button. Holding releases back has a cost, described under [Does it delay my clicks?](#faq).
- **Clicks followed by a quick move.** When you click in place and move away, the release goes out as the pointer leaves the spot, before the movement reaches apps, so the click lands where you made it.
- **Back and forward buttons** can be filtered too, and are off until you turn them on. They use the click rule only: a press inside the window of the last release is dropped. Their releases are never held back for the window, and a drag made with one isn't protected.
- **The scroll wheel** has a fix for a worn encoder that now and then reports one notch the wrong way in the middle of a scroll. With it on, a notch that goes the opposite way to the one before it and arrives within the wheel window (50 ms by default) is dropped. No notch is held back for the window. Smooth scrolling from a trackpad, a Magic Mouse or a precision touchpad is never touched. It is off until you turn it on.
- **Trackpads, touchscreens and pens** are never filtered. They have no switch to wear out, so the app tells them apart from mice and passes their clicks through. You can also pick any mouse to leave alone, and any app to leave alone while it is in front.
- **On Windows**, while the pointer is hidden (a game's mouse-look), the app never holds back or re-sends pointer movement. While it is hidden, and in a remote session, a held release isn't sent when the pointer moves: it goes out once the filter window has passed, with your next click or by a timer, wherever the pointer is then.
- **Calibration protects your double-clicks.** It measures your own double-click speed and keeps the filter window well below it.

The filter sits in the system's own input path, an event tap on macOS and a low-level mouse hook on Windows, so every app sees the filtered clicks. Clicks are timed by the events' own timestamps on macOS and by a precise clock on Windows, so a busy computer doesn't distort the gaps. Events reach the filter in the order they happened, so a held release is settled by the time of the events that follow it: once one from after the filter window arrives, no press can still be on its way to cancel it. With nothing after it, as when you click and keep the mouse still, a timer settles it after the window plus an allowance for how late this computer delivers mouse events. On macOS, clicks and pointer movement come through one event tap, so a move can never reach apps ahead of a click's release. All of it happens on your computer.

## Features

- **System-wide filtering** for the left, right and middle buttons, and for back and forward if you want them. Each button is optional and has its own window.
- **Drag protection** for the left, right and middle buttons, from the moment the button goes down.
- **Calibration** that measures one button at a time, its bounce and your double-click speed, then recommends a filter window for that button.
- **Test pane** that shows every click's release-to-press gap, for every button, with bounces highlighted.
- **Scroll-wheel fix** for a wheel that now and then jumps one notch the wrong way. Off by default.
- **Apps** list: nothing is filtered while a listed app is in front. The active app counts, not the window under the pointer.
- **Devices** list: the mice the app has seen, each of which you can leave unfiltered. Trackpads, touchscreens and pens are listed as never filtered.
- **History** of your last 30 days: bounces per 100 clicks for each button, a trend ("getting worse", "about the same", "getting better"), and how long after a release each bounce came. It stays on your computer.
- **Native on both platforms:** a System Settings-style window and menu bar item on macOS, a Windows 11 Settings-style window and notification area icon on Windows. Light and dark mode follow the system. Switches and the sidebar are exposed to screen readers such as VoiceOver and Narrator, and keyboard focus is shown.
- **Runs quietly:** lives in the menu bar or notification area, opens at login if you want, and has no Dock icon while its window is closed.
- **Signed automatic updates** from this repository's releases. Each release's checksums are signed on the maintainer's computer, with a key that never goes to GitHub or CI, and the app installs nothing that fails the check. Checking and installing can each be turned off. The signature covers updates only: the apps are not signed with an Apple or Microsoft developer certificate, as the next section explains.
- **Copy Diagnostics** puts the version, the filter's state and the app's recent log on the clipboard for a bug report.

## Download

| Platform | Get it | Notes |
| --- | --- | --- |
| **macOS 13 or later** (Apple silicon) | [DoubleClickFixer.dmg](https://github.com/arnav-goel10/mouse-double-click-fixer/releases/latest/download/DoubleClickFixer.dmg) | Open it and drag Mouse Double-Click Fixer onto Applications. |
| **Windows 10** (version 1809 or later) **or 11**, 64-bit | [DoubleClickFixer-Setup.exe](https://github.com/arnav-goel10/mouse-double-click-fixer/releases/latest/download/DoubleClickFixer-Setup.exe) | Or the [portable exe](https://github.com/arnav-goel10/mouse-double-click-fixer/releases/latest/download/DoubleClickFixer.exe), no installation needed. |

The apps are not yet signed with an Apple or Microsoft developer certificate, so the first launch takes an extra step:

- **macOS 15 and later:** open the app; macOS says it can't verify it. Choose **Done**, go to **System Settings › Privacy & Security**, scroll to the message about Mouse Double-Click Fixer, choose **Open Anyway** (it is there for about an hour after you tried) and confirm with your password. On macOS 13 and 14, Control-click the app in Applications and choose **Open** instead.
- **Permission on macOS:** turn on **Bounce Filter**, and allow Mouse Double-Click Fixer when macOS asks, under **Privacy & Security › Device Control and Data Access** (**Accessibility** on macOS 26 and earlier). macOS only lets apps you approve filter input.
- **Windows:** if SmartScreen appears, choose **More info › Run anyway**. If Smart App Control blocks the app, Windows offers no way to run it anyway; see [Installation](docs/INSTALLATION.md#smart-app-control).

Full instructions, including uninstalling: [docs/INSTALLATION.md](docs/INSTALLATION.md).

## Getting started

1. Open Mouse Double-Click Fixer and choose **Calibrate** in the sidebar. It measures the left button unless you pick another one at the top.
2. Click the pad once at a time until it has counted twelve clicks, then double-click five times at your usual speed.
3. Apply the recommendation, and turn on **Bounce Filter**. Applying also starts filtering that button. Repeat for any other button that bounces.

Close the window and the app keeps filtering from the menu bar or notification area. More detail in [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md), and help in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## FAQ

<details>
<summary><b>Will it block my real double-clicks?</b></summary>

Not once calibrated. Only a press within the filter window of the previous release is dropped, and calibration keeps that window at no more than half of your fastest measured double-click. Before you calibrate, the default 46 ms window can catch a very fast double-click, one that leaves the button up for less than 46 ms.
</details>

<details>
<summary><b>Does it delay my clicks?</b></summary>

Yes, the release of a click. Presses go straight through. The only press that waits is one that comes while a release is still on its way to apps: it goes out right after that release, usually within a millisecond or two, so apps see the two in order.

Every release of the left, right and middle buttons is held back for the filter window (46 ms until you calibrate), so a contact dropout can't end a drag. If you move the mouse or click again, the release goes out as soon as an event from after the window arrives. On a still mouse it waits a little longer: the window plus an allowance for how late your computer delivers mouse events, which the app measures as it runs and keeps between 5 and 150 ms. On a quiet computer that is the window plus about 5 ms, for example 35 ms with a 30 ms window and 65 ms with a 60 ms window. On a busy computer the allowance grows with the delay, up to 150 ms. A click made in place is released sooner, as soon as you move the pointer off it. A shorter window means a quicker release, and so does turning the filter off for the buttons that don't bounce.

Back and forward presses, scroll-wheel notches and clicks that pass untouched are not held back. On Windows, while the pointer is hidden or in a remote session, moving doesn't release a held click: it goes out with your next click or by the timer, where the pointer is then.
</details>

<details>
<summary><b>Are drags protected from the start?</b></summary>

Yes, for the left, right and middle buttons. Every release is held, so a dropout is caught however soon after the press it comes, as long as the contact comes back within the filter window. A dropout longer than the window still ends the drag.
</details>

<details>
<summary><b>Why does it need permission on macOS?</b></summary>

Blocking a click before other apps see it requires an event tap, and macOS only allows that for apps you have approved, under **Privacy & Security › Device Control and Data Access** (**Accessibility** on macOS 26 and earlier). The app asks only for mouse events, never keystrokes.
</details>

<details>
<summary><b>Will it get me flagged in games?</b></summary>

The app doesn't add clicks of its own, but it does re-send some input: a release it held back for the filter window, whatever comes while that release is on its way to apps (a press, another button's click, pointer movement), so that apps see them in the order they happened, and a move that puts the pointer back after a drag let go while moving. Apps receive these as software input (on Windows, marked as injected), and the app's hook is in the input path whenever the filter is on. Some anti-cheat systems object to one or the other, and the app can't know what yours accepts.

If a game uses anti-cheat, turn the filter off, or quit the app, before you play it. Adding the game under **Apps** stops all filtering while it is in front and holds nothing new back, but the app's hook stays installed, so it doesn't make that safe.
</details>

## Privacy

- The app receives mouse button events and pointer movement, and nothing from the keyboard. It also receives scroll-wheel events: on macOS only while the scroll-wheel fix is on, and on Windows always, though it looks at them only while the fix is on. It uses which button went up or down, when, and where the pointer was, and keeps no record of individual clicks.
- What it does keep is a running total of the bounces it blocked, and, for each button and each day, counts of presses, bounces and repaired dropouts with how long after a release each bounce came, for the History pane. That is in `wear.json` beside the settings, for up to a year, and holds no times or places of clicks.
- To tell your devices apart it reads each pointing device's name, vendor and product IDs and serial number from the system, and on Windows it uses Raw Input to learn which device a click came from. Only the devices you choose to leave unfiltered are saved, in the settings.
- To apply the Apps list it reads which app is in front, and the list of running apps when you open the add menu. It saves only the apps you add.
- No telemetry, no analytics, no accounts.
- Its only network use is the update check to this repository's GitHub releases, and downloading an update from there. Turn off **General › Check for updates automatically** and it makes no request unless you ask it to check.
- Its log records start-up, the filter starting and stopping, failures and updates, never clicks. It stays on your computer unless you paste it into a bug report. Copy Diagnostics also lists the pointing devices the app has seen, by name.

## Known limitations

- Back and forward buttons are filtered by the click rule only, so a drag made with one isn't protected. Buttons beyond those two aren't filtered.
- The scroll-wheel fix can't tell a stray notch from a deliberate reversal made within the wheel window, so a reversal that quick loses the notches that arrive inside it.
- The app is in English only.
- Some games' anti-cheat systems can object to software input, or to programs that hook input; turn the filter off before playing them. On Windows, games that read raw input receive the clicks and moves the app re-sends as injected input, not as coming from your mouse.
- On Windows, over windows of apps running as administrator, drag protection is off, because Windows doesn't let an ordinary app send input to them, and bounce may not be filtered either; that is untested. See [Troubleshooting](docs/TROUBLESHOOTING.md#windows-apps-running-as-administrator).
- The apps aren't signed with an Apple or Microsoft developer certificate. On macOS that means the app isn't notarized: the copy you drag into Applications belongs to your account, so other software running as you could change it, and a changed copy would keep the app's permission. That stays possible until the app is notarized.

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
git clone https://github.com/arnav-goel10/mouse-double-click-fixer
cd mouse-double-click-fixer
python3 -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

Run the tests with `python -m unittest discover -s tests`. Packaging and releases are covered in [docs/RELEASING.md](docs/RELEASING.md).

## Contributing

Bug reports and pull requests are welcome; start with [CONTRIBUTING.md](CONTRIBUTING.md). Please report security issues privately as described in [SECURITY.md](SECURITY.md).

## Support the project

Mouse Double-Click Fixer is free. If it saved you from buying a new mouse, star the repository.

## Star history

[![Star History Chart](https://api.star-history.com/svg?repos=arnav-goel10/mouse-double-click-fixer&type=Date)](https://star-history.com/#arnav-goel10/mouse-double-click-fixer&Date)

## License

[MIT](LICENSE) © 2026 Arnav Goel

The apps are built on Qt and Qt for Python (LGPL-3.0), Python and other open-source software; their licences are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), and each build carries its own copy (**General › Acknowledgements…**).
