# Troubleshooting

## Bounce still gets through

Raise the filter window on the **Bounce Filter** pane by 10–20 ms and try the
**Test** pane again: any bar below the dashed line would now be filtered.
Bounce is intermittent, so a calibration run can miss the worst of it.

## A real double-click was dropped

Lower the filter window. Calibration keeps it at no more than half of your
fastest measured double-click, but it can be set by hand; below about 40 ms
double-clicks are not at risk.

## Drags still let go

Drags are protected from the moment the button goes down. If the switch loses
contact for longer than your filter window, the drag still ends; raise the
window a little. If drops are long and frequent,
the switch is close to failing and replacing it (or the mouse) is the real fix.

## macOS: the filter won't turn on

macOS only lets an approved app block input. Turn on **Bounce Filter**, choose
**Open System Settings** when asked, and switch **DoubleClick Fixer** on under
**Privacy & Security › Accessibility**. The app notices within a second.

### The switch in System Settings is on, but the app still asks

The entry belongs to a different copy of the app, for example one built from
source or installed before 0.2.0, which was signed differently. Select every
**DoubleClick Fixer** entry in the list and remove it with **−**, then choose
**Open Settings…** in the app so macOS lists the copy you are running, and
switch it on. Releases from 0.2.0 on are signed with the same certificate, so
this only needs doing once.

## macOS: the app quit when I clicked the menu bar icon

That was a crash in the Qt toolkit on macOS 27, fixed in DoubleClick Fixer
0.2.3. Update to the latest release.

## macOS: it worked, then stopped

macOS pauses event taps that stall and after waking from sleep; the app
re-arms its tap automatically. If filtering stops anyway, the menu bar item
switches to off; turn the filter back on. If Accessibility was turned off for
the app, the filter comes back by itself once it is allowed again.

## Windows: it worked, then stopped

Windows quietly removes a mouse hook that is slow to answer, for example while
the PC is under heavy load. The app re-installs its hook after any slow moment
and once a minute, so filtering resumes on its own.

## Windows: apps running as administrator

Windows doesn't let an ordinary app send input to a window running as
administrator (Task Manager, an admin terminal, some installers). Over those
windows drag protection is off, so a release goes straight through rather than
risk the button seeming stuck. If bounce gets through there, run DoubleClick
Fixer as administrator as well.

## Games

The app never invents a click, but it re-sends some: a release held back for
the filter window, and whatever comes while that release is on its way (a
press, another button's click, pointer movement), so that games see them in
the order they happened. Those reach games as software input (on Windows,
marked as injected). Some anti-cheat systems watch for that; turn the filter
off before playing those games.

## I can't find the window

Closing the window hides it; the filter keeps running. Open it from the menu
bar icon (macOS) or the notification area icon (Windows), or open the app
again from Launchpad, Spotlight or the Start menu. **Quit DoubleClick Fixer**
(macOS) or **Exit** (Windows) in that menu stops the filter and closes the app.

## Updates

Updates install by themselves. If the window is open when one is ready, it
waits and installs when you close the window; **Restart Now** in **General**
installs it straight away. After the app reopens, **General** says whether the
update worked. "No published releases were found" means the app couldn't read
this repository's releases.

## Settings file

- **macOS:** `~/Library/Application Support/DoubleClickFixer/settings.json`
- **Windows:** `%APPDATA%\DoubleClickFixer\settings.json`

Quit the app and delete the file to start from defaults.

## Reporting a bug

[Open an issue](https://github.com/arnav-goel10/doubleclick-fixer/issues/new/choose)
with the app version (bottom of **General**), your OS version, your mouse
model, your filter window and what the **Test** pane shows. On macOS, crash
reports are in `~/Library/Logs/DiagnosticReports/DoubleClickFixer-*.ips`.
