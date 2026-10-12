# Troubleshooting

When something isn't right, **About › Troubleshooting › Copy Diagnostics**
(Windows: **Settings › Troubleshooting › Copy diagnostics**) puts the app's
version, your OS, the filter's state, your settings, whether a pause is on,
the state of the pause shortcut, any other mouse utility it found, the
pointing devices the app has seen (by name) and the end of the app's log on
the clipboard. Paste it into a bug report; it never contains your clicks.

Labels are written here as macOS shows them. Windows writes the same labels in
sentence case (**Copy diagnostics**, **Add app…**, **Check now**), shows
**Choose program…** where macOS shows **Choose App…**, calls the first page
**Home** (macOS: **General**) and the last **Settings** (macOS: **About**),
and has **Exit** where macOS has **Quit**.

## Bounce still gets through

Check **History** first: if a button's bounces come close to its window it
suggests a longer one ("Raise the left button to 64 ms?"), and **Apply**
sets it. Otherwise raise that button's window by 10–20 ms under **Strength ›
Custom** on **General** (**Home**). Bounce is intermittent, so a **Test and
Tune** run can miss the worst of it, and its recommendation stays well under
half your fastest double-click: you can choose a longer window in its last
step, and its second pad shows whether a given gap would be ignored. If your
mouse's own software has a debounce setting, raising it can help too.

Then check what is switched on:

- Only the left button is filtered until you turn the others on. Right,
  middle, back and forward each have a checkbox under **Buttons**.
- In **Automatic**, a button Test and Tune hasn't measured uses 46 ms.
- The filter may be paused: the menu says **Resume**, and the status line says
  "Paused until …" or "Paused while … is in front" (see [Pausing](#pausing)).
- The app in front may be on the **Exceptions › Apps** list, which filters
  nothing while it is in front (see
  [Apps](#apps-and-where-the-filter-stops)).
- The mouse may be switched off under **Exceptions › Devices**. Trackpads,
  touchscreens and pens are not filtered (on Windows, precision touchpads; see
  [Devices](#devices) for the exceptions).
- Another mouse utility may be changing clicks first (see
  [Other mouse utilities](#other-mouse-utilities)).
- Back and forward presses use the click rule only. A scroll wheel that
  jumps a notch the wrong way has a fix of its own (see
  [The scroll wheel](#the-scroll-wheel)).

## A real double-click was dropped

Run **Test and Tune** for that button, or lower its window under **Strength ›
Custom**. Test and Tune keeps the window at no more than half of your fastest
measured double-click. A window set by hand, or the 46 ms default, can be
longer than a very fast double-click leaves the button up, and a shorter
window is less likely to touch one.

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

If another mouse utility (Mac Mouse Fix, for example) smooths the scroll wheel or remaps the back and forward
buttons, it takes those events over and replaces them with its own, so the wheel fix and the side-button
filter may see nothing to act on. Turn that feature off in the other utility to let this app handle them.

The wheel fix is off until you turn on **Fix wheel reversals** under **Other
Fixes** on **General** (**Other fixes** on **Home**). It drops a notch that
goes the opposite way to the one before it and arrives within the wheel
window, 50 ms by default (10 to 150 ms; set it under **Strength › Custom**).
Vertical and horizontal scrolling are judged separately. If a quick, deliberate
reversal loses notches, lower the window; if stray notches still get through,
raise it. **History** shows how many reversals it dropped.

Smooth scrolling is never touched: a Mac trackpad's, a Magic Mouse's, and on
Windows a precision touchpad's. On macOS, with the fix off, scroll events never
reach the app.

On Windows the fix judges only a notch that a mouse itself reports to Windows
(through Raw Input). Scrolling no mouse reported, such as a precision
touchpad's, passes. A touchpad that isn't a precision one reports as a mouse,
so its scrolling can be judged like a wheel's. If **Copy Diagnostics** shows
`device lookup: raw input unavailable`, no notch has a mouse's report to go by
and the fix drops nothing.

## Apps and where the filter stops

**Exceptions › Apps** lists the apps in which nothing is filtered while they are in front.
In front means the active app, the one with the keyboard focus, not the window
under the pointer. A click that brings an app to the front is judged by the
app that was in front before it, so the first click into a listed app is still
filtered, and the first click out of it is not.

An app is listed by its bundle identifier on macOS and by its program's file
name on Windows, so two programs with the same file name in different folders
look the same to it. On Windows, an app from the Microsoft Store is found
through ApplicationFrameHost; if the app can't be found, nothing matches.

## Devices

**Exceptions › Devices** lists the pointing devices the app has seen since it started. A
mouse shows up once you click with it while the filter is on; on Windows,
moving it is enough. Switching a mouse off there is remembered, and applies
whenever it is connected. On Windows the app can apply it only once it has
read the mouse's name from the device, a moment after the mouse first reports
(when it is connected, or after the app starts), so a click made in that
moment is still filtered.

A device is known by its connection, its vendor and product IDs, and its serial
number or, if it has none, its name. Two mice of the same model with no serial
number look the same to the app, so switching one off switches both off.

Trackpads, touchscreens and pens are listed separately and have no switch.
On Windows, only precision touchpads are told apart from mice. Three things
can still get a touchpad's click filtered there:

- A tap within about a second of using a mouse is taken for that mouse's
  click. It is held for the filter window like a mouse's, and a second tap that
  follows within the window is dropped as bounce.
- A touchpad that isn't a precision one reports to Windows as a mouse. It is
  listed with the mice, filtered like one, and has a switch.
- If **Copy Diagnostics** shows `device lookup: raw input unavailable`, the app
  can't tell touchpads from mice, so it filters every touchpad click.

Touchscreens and pens always pass, because Windows marks the clicks it makes
from them.

## History

The trend on **History** compares the older half of the last 30 days with the
newer half, and says "Not enough clicks yet" until each half has at least 200
clicks. It says "Getting worse" or "Getting better" only for a change of at
least half again that is more than chance, and "About the same" otherwise. Only
buttons being filtered, from devices being filtered, are counted, and only
while the filter is on. If you changed a button's window during the 30 days,
the counts move with it, as a longer window catches more.

**About › Troubleshooting › Blocked bounces › Reset…** (Windows: **Settings ›
Troubleshooting**) starts the blocked count again and keeps the history. To
clear the history, quit the app and delete `wear.json`.

The verdict at the top of **History** is the health check. It counts only the
bounces the filter caught: one that gets past the window looks like a normal
click, so it can't be counted. That is why the check also looks at the last 14
days' bounces of each button, and, with at least 20 of them, suggests a longer
window when the longest 1 in 100 reaches past 80% of the current one. The
suggestion puts those bounces at 70% of the new window, never goes above half
your fastest double-click as Test and Tune measured it (60 ms where it hasn't
measured one), only ever raises a window, and changes nothing until you choose
**Apply**. If bounces are close to the window but no suggestion appears, a
longer window could catch your own double-clicks; run **Test and Tune**.

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
again with **Filter Bounce** in the menu bar menu.

If the filter couldn't start at all, the menu bar menu's status line and
**General** say why. A start the app makes by itself, at login or after
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
held back for the filter window, and whatever comes while that release is on
its way (a press, another button's click, and on macOS pointer movement), so
that games see them in the order they happened. On Windows the app sends no
pointer movement at all, only presses and releases: it puts the pointer where
your hand has taken it, and after a drag let go while moving on the spot where
the button came up and back, by placing it directly, which isn't input and
which no input hook sees. Games receive re-sent input as software input; on
Windows it is marked as injected, and games that read raw input see it as
injected input rather than as coming from your mouse. The app's hook is also
in the input path whenever the filter is on. Some anti-cheat systems can
object to software input or to input hooks, and the app can't know what yours
accepts. Turn the filter off, or quit the app, before playing a game that uses
one.

To keep the filter on for everything else, add the game under **Exceptions ›
Apps**. While it is in front nothing is filtered and nothing new is held back,
and a release held when you switch to it goes out first. That doesn't take the
hook out of the input path, so it isn't a way to make a game with anti-cheat
safe.

**Exceptions › Games › Pause during full-screen games** (off by default)
stops the filter while a full-screen game is in front, so nothing is held back
or re-sent, and starts it again when the game leaves the front. The app is
still running, with its pause shortcut registered and a check every two
seconds for a game, so this isn't a promise that a game's anti-cheat accepts
it either. What the check looks for:

- **macOS:** the app in front declares in its Info.plist that it is a game
  (an `LSApplicationCategoryType` that is a Games category, or
  `LSSupportsGameMode` or `GCSupportsGameMode`), and one of its windows covers
  a whole display. That is the test the system's own Game Mode is described as
  making; whether Game Mode itself is on can't be asked, so it isn't. A game
  whose bundle declares neither, or whose full-screen window belongs to a
  child process (a Java game started by a launcher), isn't seen.
- **Windows:** Windows says a full-screen exclusive Direct3D app is running
  (`SHQueryUserNotificationState`); or the window in front covers its monitor
  and either Windows says a full-screen app is running, or the program is on
  the list of games Windows keeps under
  `HKEY_CURRENT_USER\System\GameConfigStore\Children`. That key isn't
  documented by Microsoft; if it is missing, only the first two tests count.
  Browsers, video players, slide shows and meetings, remote desktop and
  virtual machine windows, the desktop, the lock screen and screen savers
  never count. A game in a browser, or in one of those programs, isn't seen,
  and another full-screen program that isn't a game may be taken for one. A
  game Windows won't name still counts when Windows says it is exclusive or
  busy full screen.

If it pauses when it shouldn't, choose **Resume** in the menu (or press the
pause shortcut): it won't pause for that program again until the program has
left the front. If it doesn't pause, turn the filter off yourself, as above.

## Pausing

**Pause for 15 Minutes** (Windows: **Pause for 15 minutes**) is in the macOS
menu bar menu and Dock menu and the Windows notification area icon's
right-click menu. The shortcut is ⌃⌥⌘P on macOS and Ctrl+Alt+Shift+P on
Windows; it pauses for 15 minutes, or resumes. While the filter is paused the
menu says **Resume** and the status line says when it ends.

- **The menu item is greyed out (macOS) or missing (Windows).** The filter is
  off, so there is nothing to pause. Turning the filter off also ends a pause.
- **The shortcut does nothing.** The filter may be off; the shortcut may be
  turned off (**General › Pause shortcut** on macOS, **Settings › Pausing**
  on Windows); or another program already holds those keys. **Copy
  Diagnostics** has a `pause shortcut` line that says which: "turned off",
  "registered", or "unavailable: Another app already uses this shortcut."
  (Windows: "Another program already uses this shortcut."). The keys can't be
  changed. On macOS the key is the one where P is on a US keyboard, so on
  another layout it is the key in that place; on Windows it is the P of your
  layout.
- **A pause ended by itself.** A pause is never saved, so quitting the app or
  restarting the computer ends it, and filtering is back on when the app next
  starts. A pause that runs out while the computer sleeps is over when it
  wakes.
- **Does it read my keyboard?** No. The app asks the system to tell it when
  that one combination is pressed (`RegisterEventHotKey` on macOS,
  `RegisterHotKey` on Windows). It installs no keyboard hook or event tap,
  and macOS asks for no Input Monitoring permission for it.

## Other mouse utilities

The app looks, every minute and when you bring it forward, at the names of the
running programs for: Logi Options+, Logitech Options, Logitech G HUB and
Razer Synapse (macOS and Windows); Mac Mouse Fix, SteerMouse, LinearMouse and
BetterMouse (macOS); X-Mouse Button Control and DoubleClickFix (Windows). If
it finds one, **General** (**Home**), the **Test and Tune** verdict,
**History** and **Copy Diagnostics** name it and say what it may take over:
the side buttons, the middle button, the wheel, or clicks (LinearMouse has a
debounce of its own, and DoubleClickFix is another bounce filter, so two
filters would judge every click). It says "may": that is what the utility can
be set to do, not what it does on your computer. The app changes nothing in
it. If side buttons or the wheel don't respond, or clicks seem filtered
twice, open that utility's settings and turn off what it does to those
buttons, or quit it and try again. A utility the app doesn't know isn't
named.

On Windows, while the pointer is hidden (as it is in most games' mouse-look),
the app never holds back or re-sends pointer movement. A held release goes out
once the filter window has passed, with your next click or by a timer,
wherever the pointer is then; the pointer is never moved. A game that keeps
the pointer showing, even drawn invisible, doesn't count as hidden: the app
treats it as an ordinary pointer, and may hold back and re-send its movement
for a moment after a click.

## Open at login doesn't open the app

**General › Open at login** (Windows: **Start with Windows**, in the tray menu
and under **Settings › Startup**) reads the system's own setting. If it says the app
is turned off in **System Settings › General › Login Items** (macOS) or in
**Task Manager › Startup apps** (Windows), that list wins. On macOS, choose
**Open Login Items…** and turn the app on there. On Windows, turn the switch
in the app on again, which clears Task Manager's choice. A copy run from the
disk image can't open at login; move it to Applications first.

## I can't find the window

Closing the window hides it; the filter keeps running. On macOS, choose
**Settings…** (⌘,) in the menu bar icon's menu, or open the app again from
Spotlight, Applications or the Dock. On Windows, left-click the notification
area icon and choose **Open** in its flyout, or double-click the icon, or
choose **Open** in its right-click menu. **Quit Mouse Double-Click Fixer**
(macOS) or **Exit** (Windows) in that menu stops the filter and closes the
app. If the menu bar icon is hidden (see **General › Show in menu bar**, and
**System Settings › Menu Bar** on macOS 26 and later), the Dock icon stays and
opening the app again opens Settings.

## Updates

Updates install by themselves while **Check for updates automatically** and
**Install updates automatically** are both on (**About › Software Update** on
macOS, **Settings › Updates** on Windows). If the window is open when one is
ready, it waits and installs when you close the window; **Restart Now**
(Windows: **Restart now**) installs it straight away. After the app reopens,
**About** (Windows: **Settings**) says whether the update worked.

What the messages under **Software Update** (Windows: **Updates**) mean:

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
| `wear.json` | The History: daily counts for each button and for the scroll wheel, kept for a year. Quit the app and delete it to clear the History. |
| `wear.json.bak` | The previous good history, used if `wear.json` is damaged. |
| `window.json` | On macOS, the Settings pane last shown, so Settings opens on it again. Delete it to forget that. |
| `DoubleClickFixer.log` | Start-up, the filter starting and stopping, pauses, whether the pause shortcut registered, the other mouse utilities found, permission changes, waking from sleep and switching users, the TLS library in use, failures and updates; never clicks. Older lines move to `DoubleClickFixer.log.1` and `.2`. |
| `crash.log` | A line per launch, and the details of any hard crash. |

On macOS, system crash reports are in
`~/Library/Logs/DiagnosticReports/DoubleClickFixer-*.ips`.

## Reporting a bug

[Open an issue](https://github.com/arnav-goel10/mouse-double-click-fixer/issues/new/choose)
and paste what **Copy Diagnostics** gives you. Add your mouse model, what you
did and what happened, and what **Test and Tune** says if it is about
filtering.
