# Installation

## macOS

Requires macOS 13 Ventura or later on an Apple silicon Mac (M1 or newer).

1. Download [DoubleClickFixer.dmg](https://github.com/arnav-goel10/mouse-double-click-fixer/releases/latest/download/DoubleClickFixer.dmg).
2. Open it and drag **Mouse Double-Click Fixer** onto **Applications**.
3. Open the app from Applications or Spotlight. It is not notarized yet, so
   the first time macOS won't open it:
   - **macOS 15 and later:** macOS says it can't verify the app. Choose
     **Done**, then go to **System Settings › Privacy & Security**, scroll to
     the message about Mouse Double-Click Fixer, choose **Open Anyway** and
     confirm with your password. The button is there for about an hour after
     you tried to open the app; if it's gone, open the app again first.
   - **macOS 13 and 14:** Control-click (or right-click) the app in
     Applications, choose **Open**, then **Open** again.
4. The first window explains why the app needs permission. Choose
   **Continue**. macOS asks: choose **Open System Settings** and switch
   **Mouse Double-Click Fixer** on under **Privacy & Security › Device
   Control and Data Access** (on macOS 26 and earlier the list is called
   **Accessibility**). The window notices within a second, says **Bounce
   Filter is on** and starts filtering. It then offers **Test and Tune…**
   (see [Getting started](GETTING_STARTED.md#2-test-and-tune)).

Mouse Double-Click Fixer is a menu bar app: with its window closed it has no
Dock icon and runs from the mouse icon in the menu bar, which opens a menu
(**Filter Bounce**, **Pause for 15 Minutes**, **Test and Tune…**,
**Settings…**, **Quit**). While Settings is open it has a Dock icon too. Once the installed app
starts, it ejects the installer disk image and moves it to the Trash if it is
in Downloads.

Run the app from Applications, not from the disk image: a copy run from the
disk image can't update itself or open at login.

## Windows

Requires 64-bit (x64) Windows 10 version 1809 or later, or Windows 11.

1. Download [DoubleClickFixer-Setup.exe](https://github.com/arnav-goel10/mouse-double-click-fixer/releases/latest/download/DoubleClickFixer-Setup.exe)
   and run it. It installs for your account and doesn't need administrator
   rights. If SmartScreen warns about an unknown publisher, choose
   **More info › Run anyway**.
2. Choose whether to start Mouse Double-Click Fixer when you sign in, and
   whether to add a desktop shortcut.
3. Open it from the Start menu and turn on **Bounce filter** on **Home**.

With the window closed, the app runs from its icon in the notification area
(it may be under the **^** overflow arrow; drag it next to the clock to keep
it in view). Left-click the icon for a small flyout with the **Bounce filter**
switch and **Open**; right-click it for **Open**, **Pause for 15 minutes**,
**Start with Windows** and **Exit**.

The [portable DoubleClickFixer.exe](https://github.com/arnav-goel10/mouse-double-click-fixer/releases/latest/download/DoubleClickFixer.exe)
is the same app without an installer. It unpacks itself each time it starts,
so it starts more slowly than the installed copy. Keep it somewhere
permanent, since it updates itself in place.

### Smart App Control

The Windows builds are not yet signed with a code-signing certificate.
SmartScreen lets you run them anyway, but Smart App Control (Windows 11)
blocks unsigned apps and offers no way to run one. If it blocks Mouse
Double-Click Fixer, your options are to turn Smart App Control off in
**Windows Security › App & browser control › Smart App Control settings**, or
to wait for signed builds. Check what turning it off means on your version of
Windows first: on some, it can't be turned back on without reinstalling
Windows.

## Updates

With **Check for updates automatically** on (**About › Software Update** on
macOS, **Settings › Updates** on Windows), the app looks for a new
release 20 seconds after it starts and every six hours. With **Install
updates automatically** on as well, it downloads the update, verifies it,
installs it and reopens itself, usually within a few seconds. If its window
is open, it waits and installs when you close it; **Restart Now** (Windows:
**Restart now**) installs it straight away. On macOS the permission carries
over.

The app was called DoubleClick Fixer before 1.0. Updating keeps its
settings, its permission on macOS and **Open at login**. On macOS, a copy that
updates itself from 0.5.3 or earlier to 1.0 keeps its old name in
Applications, and takes the new one with its next update; a copy you renamed
yourself keeps your name. On Windows, an update stays in the folder the app
was installed in and gives its Start menu shortcut the new name. The shortcut
moves from the DoubleClick Fixer folder to a Mouse Double-Click Fixer one; a
Start menu folder you chose yourself is kept.

Every update must carry a signature from the project's release keys, which
are built into the app; anything else is refused (see
[SECURITY.md](../SECURITY.md)). Copies older than 1.0 check only the
release's checksums (on macOS, plus the code-signature check) for the update
that brings them to 1.0.

To update by hand, turn off **Install updates automatically** and use
**Check Now** (Windows: **Check now**) in the same place. On macOS, **Check for
Updates…** in the app menu (the menu named for the app, in the menu bar while
Settings is open) does the same.

Going back to 0.5.3 works, with two losses. It has one window for every button
and takes the left button's. It doesn't know the back and forward buttons, so
it drops them from the filtered buttons the next time it saves. 1.0 reads the
rest of its settings again.

## Uninstall

- **macOS:** turn off **General › Open at login**, quit the app with **Quit
  Mouse Double-Click Fixer** in its menu bar icon's menu, then drag it from Applications to the Trash (a copy updated from
  0.5.3 or earlier may still be called DoubleClick Fixer there). Remove **Mouse
  Double-Click Fixer** from **Privacy & Security › Device Control and Data
  Access** (**Accessibility** on macOS 26 and earlier) with the **−** button.
  Its settings, history and log stay in
  `~/Library/Application Support/DoubleClickFixer` until you delete that
  folder. Or run `bash installer/uninstall_macos.sh` from a checkout of this
  repository: it quits the app and waits until it has exited, then removes
  the app, under either name, its login item, settings, history and
  permission.
- **Windows:** **Settings › Apps › Installed apps › Mouse Double-Click Fixer ›
  Uninstall**. This closes the app if it is running, then removes it, its
  shortcuts, the startup entry, its settings, history and log. For the
  portable exe, turn off **Settings › Startup › Start with Windows** if you
  turned it on, then choose **Exit** from its notification area icon's
  right-click menu, then delete the file and
  `%APPDATA%\DoubleClickFixer`.

## From source

See [Build from source](../README.md#build-from-source). When running from
source on macOS, the permission belongs to the app that started Python
(usually Terminal or your editor), so grant it there, or build the app with
`bash installer/build_macos.sh` and grant it to Mouse Double-Click Fixer. A
copy run from source never updates itself.
