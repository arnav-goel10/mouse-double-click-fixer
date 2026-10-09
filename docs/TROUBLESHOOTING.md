# Troubleshooting

When something isn't right, **General › Troubleshooting › Copy Diagnostics**
(Windows: **Copy diagnostics**) puts the app's version, your OS, the filter's
state, your settings and the end of the app's log on the clipboard. Paste it
into a bug report; it never contains your clicks.

## Bounce still gets through

Raise the filter window on the **Bounce Filter** pane by 10–20 ms and try the
**Test** pane again: any bar below the dashed line would now be filtered.
Bounce is intermittent, so a calibration run can miss the worst of it. If
your mouse's own software has a debounce setting, raising it can help too.

Side buttons (back and forward) and the scroll wheel are not filtered.

## A real double-click was dropped

Lower the filter window, or calibrate. Calibration keeps it at no more than
half of your fastest measured double-click, but it can be set by hand; below
about 40 ms double-clicks are not at risk.

## Drags still let go

Drags are protected from the moment the button goes down. If the switch loses
contact for longer than your filter window, the drag still ends; raise the
window a little. If drops are long and frequent, the switch is close to
failing and replacing it (or the mouse) is the real fix.

## The pointer jumps back for a moment after a drag

When a drag ends while the mouse is still moving, the release is delivered
where you let go of the button. That takes the pointer there for an instant,
and the app then puts it back where your hand has taken it.

## macOS: the filter won't turn on

macOS only lets an approved app block input. Turn on **Bounce Filter**, choose
**Open System Settings** when asked, and switch **DoubleClick Fixer** on under
**Privacy & Security › Device Control and Data Access** (on macOS 26 and
earlier, **Privacy & Security › Accessibility**). The app notices within a
second. Until then the menu bar menu says "Waiting for … permission".

### The switch in System Settings is on, but the app still asks

The entry belongs to a different copy of the app, for example one built from
source or installed before 0.2.0, which was signed differently. Select every
**DoubleClick Fixer** entry in the list and remove it with **−**, then choose
**Open Settings…** in the app so macOS lists the copy you are running, and
switch it on. Releases from 0.2.0 on are signed with the same certificate, so
this only needs doing once.

## macOS: the permission was turned off

If you remove DoubleClick Fixer from the list, or turn its switch off, while
it is filtering, the filter stops within a second and clicks go through
untouched. Your choice to filter is kept: the menu says it is waiting for
permission, and filtering starts again by itself once you allow the app.

## macOS: it worked, then stopped

macOS pauses event taps that stall, and after waking from sleep. The app
re-arms its tap, checks it every few seconds, and rebuilds the filter after
sleep and when you switch back to your user account. If macOS switches the
filter off three times within 30 seconds, the app stops filtering rather than
fight the system, and says "macOS kept switching the filter off"; turn it on
again from the menu bar.

If the filter couldn't start at all, the menu bar menu and the **Bounce
Filter** pane say why. A start the app makes by itself, at login or after
sleep, is tried again 1, 3, 8 and 20 seconds later before it gives up.

## Windows: it worked, then stopped

Windows quietly removes a mouse hook that is slow to answer, for example while
the PC is under heavy load. The app re-installs its hook after any slow
moment, every 15 seconds, and when you unlock the PC, reconnect to it or wake
it, so filtering resumes on its own.

## Windows: apps running as administrator

Windows doesn't let an ordinary app send input to a window running as
administrator (Task Manager, an admin terminal, some installers). Over those
windows the app can't hold a release back and send it later, so drag
protection is off there and a release goes straight through rather than leave
the button looking stuck. Bounce is still filtered. If drags over such a
window still let go, run DoubleClick Fixer as administrator as well.

## Games

The app never invents a click, but it re-sends some input: a release it held
back, a press or pointer movement that waited behind it, and a move that puts
the pointer back after a drag let go while moving. Games receive these as
software input; on Windows they are marked as injected, and games that read
raw input see them as injected input rather than as coming from your mouse.
Some anti-cheat systems watch for software input; turn the filter off before
playing those games.

On Windows, while the pointer is hidden (a game's mouse-look), in a remote
session, and for pen or touch input, pointer movement never waits behind a
held release. The release goes out once the filter window has passed, with
your next click or by a timer, wherever the pointer is then; the pointer is
never moved.

## Open at login doesn't open the app

**General › Open at login** reads the system's own setting. If it says the app
is turned off in **System Settings › General › Login Items** (macOS) or in
**Task Manager › Startup apps** (Windows), that list wins. On macOS, choose
**Open Login Items…** and turn the app on there. On Windows, turn the switch
in the app on again, which clears Task Manager's choice. A copy run from the
disk image can't open at login; move it to Applications first.

## I can't find the window

Closing the window hides it; the filter keeps running. Open it from the menu
bar icon (macOS) or the notification area icon (Windows), or open the app
again from Spotlight, Applications or the Start menu. **Quit DoubleClick
Fixer** (macOS) or **Exit** (Windows) in that menu stops the filter and closes
the app.

## Updates

Updates install by themselves while **General › Check for updates
automatically** and **Install updates automatically** are both on. If the
window is open when one is ready, it waits and installs when you close the
window; **Restart Now** (Windows: **Restart now**) installs it straight away.
After the app reopens, **General** says whether the update worked.

What the messages in **General › Software update** mean:

- **No published releases were found.** The app couldn't read this
  repository's releases, for example because none is published yet.
- **This update isn't signed, so it can't be installed.** (or its signature
  isn't valid, or is for another version.) The release doesn't carry a valid
  signature from the project's keys, so the app won't download it.
- **Move DoubleClick Fixer to Applications to update it.** The app is running
  from the disk image or from where macOS put a downloaded copy it hasn't
  moved. Drag it to Applications and open it from there.
- **Your account can't change apps in Applications.** Ask an administrator to
  update the app.
- **No permission to replace the app in …** The folder the app runs from
  can't be written by your account. Move the app somewhere you can write, or
  install it again from the latest release.
- An update that failed to install twice is not tried again by itself; choose
  **Update Now** (Windows: **Update now**) to try it.

## Settings, logs and crash reports

The app keeps its files in one folder:

- **macOS:** `~/Library/Application Support/DoubleClickFixer/`
- **Windows:** `%APPDATA%\DoubleClickFixer\`

| File | What it is |
| --- | --- |
| `settings.json` | Your settings. Quit the app and delete it to start from defaults. |
| `settings.json.bak` | The previous good settings, used if `settings.json` is damaged. |
| `DoubleClickFixer.log` | Start-up, the filter starting and stopping, failures and updates; never clicks. Older lines move to `DoubleClickFixer.log.1` and `.2`. |
| `crash.log` | A line per launch, and the details of any hard crash. |

On macOS, system crash reports are in
`~/Library/Logs/DiagnosticReports/DoubleClickFixer-*.ips`.

## Reporting a bug

[Open an issue](https://github.com/arnav-goel10/doubleclick-fixer/issues/new/choose)
and paste what **Copy Diagnostics** gives you. Add your mouse model, what you
did and what happened, and what the **Test** pane shows if it is about
filtering.
