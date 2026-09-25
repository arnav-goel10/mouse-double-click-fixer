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

Drags are protected once the button has been held for about 30 ms, a
fraction of a click. If the switch loses contact for longer than your filter window, the
drag still ends; raise the window a little. If drops are long and frequent,
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
switches to off; turn the filter back on.

## Windows: nothing is filtered in one particular app

A low-level mouse hook cannot filter input for windows running as
administrator unless DoubleClick Fixer also runs as administrator.

## Games

Clicks are only ever blocked, never generated, except releases, which the app
holds for the filter window and re-sends. Some anti-cheat systems watch
for software-sent input; turn the filter off before playing those games.

## I can't find the window

Closing the window hides it; the filter keeps running. Open it from the menu
bar icon (macOS) or the notification area icon (Windows), or open the app
again from Launchpad, Spotlight or the Start menu. **Quit** in that menu stops
the filter and exits.

## Settings file

- **macOS:** `~/Library/Application Support/DoubleClickFixer/settings.json`
- **Windows:** `%APPDATA%\DoubleClickFixer\settings.json`

Quit the app and delete the file to start from defaults.

## Reporting a bug

[Open an issue](https://github.com/arnav-goel10/doubleclick-fixer/issues/new/choose)
with the app version (bottom of **General**), your OS version, your mouse
model, your filter window and what the **Test** pane shows. On macOS, crash
reports are in `~/Library/Logs/DiagnosticReports/DoubleClickFixer-*.ips`.
