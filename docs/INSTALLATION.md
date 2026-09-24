# Installation

## macOS

Requires macOS 11 or later on an Apple silicon Mac (M1 or newer).

1. Download [DoubleClickFixer.dmg](https://github.com/arnav-goel10/doubleclick-fixer/releases/latest/download/DoubleClickFixer.dmg).
2. Open it and drag **DoubleClick Fixer** onto **Applications**.
3. Open the app from Launchpad or Spotlight. The app is not notarized yet, so
   the first time macOS may refuse to open it: right-click the app and choose
   **Open**, or use **System Settings › Privacy & Security › Open Anyway**.
4. Turn on **Bounce Filter**. macOS asks for Accessibility access: choose
   **Open System Settings** and switch **DoubleClick Fixer** on. The app notices
   within a second and starts filtering.

DoubleClick Fixer is a menu bar app: with its window closed it has no Dock
icon and runs from the mouse icon in the menu bar. Once the installed app
starts, it ejects the installer disk image and moves it to the Trash if it is
in Downloads.

## Windows

Requires Windows 10 or 11.

1. Download [DoubleClickFixer-Setup.exe](https://github.com/arnav-goel10/doubleclick-fixer/releases/latest/download/DoubleClickFixer-Setup.exe)
   and run it. If SmartScreen warns about an unknown publisher, choose
   **More info › Run anyway**.
2. Choose whether to start DoubleClick Fixer when you sign in, and whether to
   add a desktop shortcut.
3. Open it from the Start menu and turn on **Bounce Filter**.

With the window closed, the app runs from its icon in the notification area
(it may be under the **^** overflow arrow).

The [portable DoubleClickFixer.exe](https://github.com/arnav-goel10/doubleclick-fixer/releases/latest/download/DoubleClickFixer.exe)
is the same app without an installer; keep it somewhere permanent, since it
updates itself in place.

## Updates

The app checks for a new release shortly after launch and every six hours,
downloads it, verifies it, installs it and reopens itself, usually within a
few seconds. On macOS the Accessibility permission carries over. To update by
hand instead, turn off **General › Install updates automatically** and use
**Check Now**.

## Uninstall

- **macOS:** quit the app from its menu bar icon, then drag it from
  Applications to the Trash. To remove its settings and login item as well,
  run `bash installer/uninstall_macos.sh` from a checkout of this repository.
  Remove **DoubleClick Fixer** from **Privacy & Security › Accessibility**
  with the **−** button.
- **Windows:** **Settings › Apps › Installed apps › DoubleClick Fixer ›
  Uninstall**. This removes the app, its shortcuts, the startup entry and
  saved settings.

## From source

See [Build from source](../README.md#build-from-source). When running from
source on macOS, the Accessibility permission belongs to the app that started
Python (usually Terminal or your editor), so grant it there, or build the app
with `bash installer/build_macos.sh` and grant it to DoubleClick Fixer.
