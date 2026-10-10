# Troubleshooting

When something isn't right, **General › Troubleshooting › Copy Diagnostics**
(Windows: **Copy diagnostics**) puts the app's version, your OS, the filter's
state, your settings, the pointing devices the app has seen (by name) and the
end of the app's log on the clipboard. Paste it into a bug report; it never
contains your clicks.

Buttons are written here as macOS shows them. Windows writes the same labels
in sentence case (**Copy diagnostics**, **Add app…**, **Check now**), shows
**Choose program…** where macOS shows **Choose App…**, and ends the
notification area menu with **Exit**.

## Bounce still gets through

Raise that button's filter window on the **Bounce Filter** pane by 10–20 ms
and try the **Test** pane again: any bar below the dashed line would now be
filtered. Bounce is intermittent, so a calibration run can miss the worst of
it. If your mouse's own software has a debounce setting, raising it can help
too.

Then check what is switched on:

- Only the left button is filtered until you turn the others on. Right,
  middle, back and forward each have a switch and a window of their own.
- The app in front may be on the **Apps** list, which filters nothing while it
  is in front (see [Apps](#apps-and-where-the-filter-stops)).
- The mouse may be switched off under **Devices**. Trackpads, touchscreens and
  pens are never filtered.
- Back and forward presses use the click rule only. A scroll wheel that
  jumps a notch the wrong way has a fix of its own (see
  [The scroll wheel](#the-scroll-wheel)).

## A real double-click was dropped

Lower that button's filter window, or calibrate it. Calibration keeps the
window at no more than half of your fastest measured double-click. A window
set by hand, or the 46 ms default, can be longer than a very fast double-click
leaves the button up, and a shorter window is less likely to touch one.

## Drags still let go

Drags made with the left, right or middle button are protected from the moment
the button goes down. If the switch loses contact for longer than your filter
window, the drag still ends; raise the window a little. If drops are long and
frequent, the switch is close to failing and replacing it (or the mouse) is
the real fix. Back and forward never hold a release, so a drag made with one
isn't protected.

## The pointer jumps back for a moment after a drag

When a drag ends while the mouse is still moving, the release is delivered
where you let go of the button. That takes the pointer there for an instant,
and the app then puts it back where your hand has taken it.

## The scroll wheel

The wheel fix is off until you turn it on under **Bounce Filter › Scroll
wheel**. It drops a notch that goes the opposite way to the one before it and
arrives within the wheel window, 50 ms by default (10 to 150 ms can be set).
Vertical and horizontal scrolling are judged separately. If a quick, deliberate
reversal loses notches, lower the window; if stray notches still get through,
raise it. **History** shows how many reversals it dropped.

Smooth scrolling is never touched: a trackpad's, a Magic Mouse's, a
touchpad's. On macOS, with the fix off, scroll events never reach the app.

## Apps and where the filter stops

**Apps** lists the apps in which nothing is filtered while they are in front.
In front means the active app, the one with the keyboard focus, not the window
under the pointer. A click that brings an app to the front is judged by the
app that was in front before it, so the first click into a listed app is still
filtered, and the first click out of it is not.

An app is listed by its bundle identifier on macOS and by its program's file
name on Windows, so two programs with the same file name in different folders
look the same to it. On Windows, an app from the Microsoft Store is found
through ApplicationFrameHost; if the app can't be found, nothing matches.

## Devices

**Devices** lists the pointing devices the app has seen since it started. A
mouse shows up once you click with it while the filter is on. Switching a
mouse off there is remembered, and applies whenever it is connected.

A device is known by its connection, its vendor and product IDs, and its serial
number or, if it has none, its name. Two mice of the same model with no serial
number look the same to the app, so switching one off switches both off.

Trackpads, touchscreens and pens are listed as never filtered and have no
switch.

## History

The trend on **History** compares the older half of the last 30 days with the
newer half, and says "Not enough clicks yet" until each half has at least 200
clicks. It says "Getting worse" or "Getting better" only for a change of at
least half again that is more than chance, and "About the same" otherwise. Only
buttons being filtered, from devices being filtered, are counted, and only
while the filter is on. If you changed a button's window during the 30 days,
the counts move with it, as a longer window catches more.

**General › Blocked bounces › Reset…** starts the blocked count again and keeps
the history. To clear the history, quit the app and delete `wear.json`.

## macOS: the filter won't turn on

macOS only lets an approved app block input. Turn on **Bounce Filter**, choose
**Open System Settings** when asked, and switch **Mouse Double-Click Fixer**
on under **Privacy & Security › Device Control and Data Access** (on macOS 26
and earlier, **Privacy & Security › Accessibility**). The app notices within a
second. Until then the menu bar menu says "Waiting for … permission".

### The switch in System Settings is on, but the app still asks

The entry belongs to a different copy of the app, for example one built from
source or installed before 0.2.0, which was signed differently. Select every
**Mouse Double-Click Fixer** entry in the list, and any **DoubleClick Fixer**
one (the app's name before 1.0), and remove it with **−**, then choose
**Open Settings…** in the app so macOS lists the copy you are running, and
switch it on. Releases from 0.2.0 on are signed with the same certificate, so
this only needs doing once.

## macOS: the permission was turned off

If you remove Mouse Double-Click Fixer from the list, or turn its switch off,
while it is filtering, the filter stops within a second and clicks go through
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
the button looking stuck. Whether bounce is still filtered over such a window
hasn't been tested: Windows may not call an ordinary app's mouse hook for
input going to a window with higher rights, in which case nothing is filtered
there. If drags or bounce over such a window are a problem, run Mouse
Double-Click Fixer as administrator as well.

## Windows: Remote Desktop

Run Mouse Double-Click Fixer on the computer the mouse is plugged into, not on
the one you connect to. In a remote session the Remote Desktop client
positions the pointer, so the app doesn't send a held release to the spot where
it happened: the release goes out once the filter window has passed, with your
next click or by a timer, wherever the pointer is then.

## Games

The app doesn't add clicks of its own, but it re-sends some input: a release
held back for the filter window, whatever comes while that release is on its
way (a press, another button's click, pointer movement), so that games see
them in the order they happened, and a move that puts the pointer back after a
drag let go while moving. Games receive these as software input; on Windows
they are marked as injected, and games that read raw input see them as
injected input rather than as coming from your mouse. The app's hook is also
in the input path whenever the filter is on. Some anti-cheat systems can
object to software input or to input hooks, and the app can't know what yours
accepts. Turn the filter off, or quit the app, before playing a game that uses
one.

To keep the filter on for everything else, add the game under **Apps**. While
it is in front nothing is filtered and nothing new is held back, and a
release held when you switch to it goes out first. That doesn't take the hook
out of the input path, so it isn't a way to make a game with anti-cheat safe.

On Windows, while the pointer is hidden (a game's mouse-look), the app never
holds back or re-sends pointer movement. A held release goes out once the
filter window has passed, with your next click or by a timer, wherever the
pointer is then; the pointer is never moved. Touchpads, touchscreens and pens
are never filtered.

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
again from Spotlight, Applications or the Start menu. **Quit Mouse
Double-Click Fixer** (macOS) or **Exit** (Windows) in that menu stops the
filter and closes the app.

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
- **Move Mouse Double-Click Fixer to Applications to update it.** The app is
  running from the disk image or from where macOS put a downloaded copy it
  hasn't moved. Drag it to Applications and open it from there.
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
| `wear.json` | The History: daily counts for each button, kept for a year. Quit the app and delete it to clear the History. |
| `wear.json.bak` | The previous good history, used if `wear.json` is damaged. |
| `DoubleClickFixer.log` | Start-up, the filter starting and stopping, failures and updates; never clicks. Older lines move to `DoubleClickFixer.log.1` and `.2`. |
| `crash.log` | A line per launch, and the details of any hard crash. |

On macOS, system crash reports are in
`~/Library/Logs/DiagnosticReports/DoubleClickFixer-*.ips`.

## Reporting a bug

[Open an issue](https://github.com/arnav-goel10/mouse-double-click-fixer/issues/new/choose)
and paste what **Copy Diagnostics** gives you. Add your mouse model, what you
did and what happened, and what the **Test** pane shows if it is about
filtering.
