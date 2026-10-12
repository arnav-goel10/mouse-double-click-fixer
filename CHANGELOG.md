# Changelog

## 1.1.0 — 2026-10-12

### Redesign

- **A simpler layout on both systems.** Most people need one thing: the filter
  on, and a way to know it works. That is now the first thing on screen, and
  the rest is one level down. The filter itself and its timing are
  unchanged, and your settings carry over.
- **macOS: the menu bar icon opens a menu.** From the top: a status line
  ("Filtering · 1,981 bounces blocked"), **Filter Bounce** with a checkmark,
  **Pause for 15 Minutes** (**Resume** while paused), **Test and Tune…**,
  **Settings…** (⌘,) and **Quit Mouse Double-Click Fixer**. The menu no longer
  has update items; updates are under **About**, and **Check for Updates…**
  stays in the app's own menu.
- **macOS: Settings has four panes in a toolbar.** **General** (the filter's
  switch and status, **Buttons**, **Strength**, **Test and Tune…**, **Other
  Fixes**, the pause shortcut, **Open at login**, **Show in menu bar**),
  **Exceptions** (**Apps**, **Games**, **Devices**), **History** and
  **About** (updates, **Copy Diagnostics**, the blocked count, privacy,
  **Acknowledgements…**). The window is titled with the pane, sized to it, and
  opens on the pane you last used.
- **macOS: a Dock menu** with the same actions as the menu bar menu. If you
  hide the menu bar icon with **Show in menu bar**, the Dock icon stays, and opening the app again opens Settings.
- **macOS: a first-run window.** It explains the permission in a few words,
  has one **Continue** button that brings up macOS's own request, then shows
  "Waiting for permission…" and "Ready", and offers **Test and Tune…**.
- **Windows: the notification area icon has a flyout and a short menu.** A left
  click opens a flyout with the status, the **Bounce filter** switch and
  **Open**; it closes when you click elsewhere. A right click opens **Open**,
  **Pause for 15 minutes** (**Resume** while paused), **Start with Windows**
  and **Exit**. The tooltip reads "Mouse Double-Click Fixer: on". The menu no
  longer has update items; updates are under **Settings**.
- **Windows: the window has navigation on the left.** **Home** (the switch and
  status, **Test and tune**, **Buttons**, **Strength**, **Other fixes**),
  **Exceptions** (**Apps**, **Games**, **Devices**), **History**, and
  **Settings** at the bottom (**Start with Windows**, pause shortcut, updates,
  **Copy diagnostics**, the blocked count, about and **Acknowledgements…**).
  Changes apply as you make them. The first time, a note on **Home** says how
  to pin the icon next to the clock.
- **The Test and Calibrate panes are now one task, Test and Tune.** See New.
- **Each button's filter window moved.** A button is now a checkbox under
  **Buttons**, marked **Calibrated** once Test and Tune has measured it. The
  windows themselves are under **Strength**. Nothing about a window changes
  when you update: every window you had is kept (see Strength below).
- **Apps and Devices are now halves of Exceptions**, with the new **Games**
  switch between them. Updates, **Copy Diagnostics**, the blocked count and
  **Acknowledgements…** are under **About** (Windows: **Settings**).

### New

- **Strength: Automatic or Custom.** **Automatic** uses what Test and Tune
  measured for each button, or 46 ms where it hasn't measured one. **Custom**
  shows a window for each filtered button, in milliseconds, for you to set.
  Each keeps its own windows, so switching back finds them as you left them.
  From 1.0, you start in Custom only if a button you filter has a window you
  set by hand; otherwise in Automatic. Every window is kept either way.
- **Test and Tune.** One window (macOS) or dialog (Windows), opened from the
  menu bar menu, **General** (**Home**) or the first-run window. Step 1: click
  a pad, once at a time and then in double-clicks, for any of the five
  buttons. Step 2: a verdict in plain words, "Your left button double-clicks
  on its own" or "No bounce seen", with your fastest double-click, what the
  filter has caught in everyday use, and any other mouse utility that may be
  in the way. Step 3: choose a window (a recommended one is marked) and try it
  on a second pad. **Apply** sets that button's window, marks it
  **Calibrated** and starts filtering it. Filtering pauses only while step 1
  is measuring with its window in front.
- **Pause for 15 minutes.** From the menu bar menu and Dock menu (macOS) or the
  notification area icon's right-click menu (Windows), or with a shortcut,
  ⌃⌥⌘P on macOS and Ctrl+Alt+Shift+P on Windows, which also resumes. The
  status line says when it ends. A pause is not saved: quitting or restarting
  ends it. The shortcut can be turned off (**General › Pause shortcut** on
  macOS, **Settings › Pausing** on Windows). It reads no keystrokes: the app
  asks the system to say when that one combination is pressed
  (`RegisterEventHotKey` on macOS, `RegisterHotKey` on Windows), installs no
  keyboard hook or tap, and macOS needs no Input Monitoring permission for it.
- **Pause during full-screen games**, under **Exceptions › Games**. Off by
  default. Every two seconds it asks whether a full-screen game is in front;
  if so, the filter stops, and it starts again when the game leaves the front.
  **Resume** puts it back sooner. On macOS a game is a front app whose bundle
  says it is one (a Games category or a Game Mode request) with a window that
  covers a display. On Windows it is a full-screen exclusive Direct3D app, or
  a window that covers its monitor and either Windows says a full-screen app
  is running or the program is on Windows' own list of games; browsers, video
  players, slide shows, meetings, remote desktop and virtual machine windows
  and Windows' own screens never count. It can miss a game or take another
  full-screen program for one, and it is not a promise that a game's
  anti-cheat accepts the app.
- **A health check in History.** The top of **History** says in plain words
  whether your mouse is bouncing ("Your left button double-clicks on its own:
  caught 14 of 14", or "No bounce seen"). The count is of bounces the filter
  caught. When a button's recent bounces come close to its window, History
  suggests a longer one ("Raise the left button to 64 ms?") with an **Apply**
  button. It never goes above half your fastest double-click as Test and Tune
  measured it (60 ms if it hasn't measured one), and changes nothing until you choose **Apply**.
- **Notes about other mouse utilities.** If Logi Options+, Logitech Options,
  Logitech G HUB, Razer Synapse, Mac Mouse Fix, SteerMouse, LinearMouse,
  BetterMouse, X-Mouse Button Control or DoubleClickFix is running, the app
  says so on **General** (**Home**), in Test and Tune, in History and in Copy
  Diagnostics, and says what that utility may take over (side buttons, the
  middle button, the wheel, or clicks). It reads only the names of running
  programs, once a minute and when you bring the app forward, and changes
  nothing in the other utility.
- **A plain status line.** The main window and the Windows tray flyout say
  what the filter has done today, for example "Your left button bounced 14
  times today. Every bounce was caught." or "No extra clicks seen yet."
- **Copy Diagnostics** now also says whether a pause is on, the state of the
  pause shortcut and of the game pause, the strength, and any other mouse
  utility found.
- **A new small file, `window.json`,** beside the settings remembers which
  Settings pane you last had open on macOS. Uninstalling removes it with the
  rest of the folder.

### Fixes

- **macOS: a hidden menu bar icon no longer leaves the app out of reach.**
  Closing the window used to remove the Dock icon whether or not the menu bar
  icon was showing. Now, with the menu bar icon hidden, the Dock icon stays,
  and its menu has the menu bar menu's actions.

## 1.0.1 — 2026-10-12

- **Windows: the pointer no longer moves like a slide show after a click.**
  Moving the mouse right after a click could freeze the pointer for about
  150 ms at a time and then make it jump, for as long as the mouse kept
  moving. Windows passes on only part of the tag the app puts on the input it
  re-sends, so the app never saw that input arrive and let what waited behind
  it, your pointer movement included, go only after a time-out. The app now
  knows its input has arrived as soon as Windows has delivered it, and it no
  longer re-sends pointer movement at all: a move that has to wait while a
  click's release goes out waits about a millisecond, and the pointer is then
  put where your hand has taken it.
- Windows: movement after a click or a drag no longer goes missing. A
  re-sent move could land after one of yours and undo it, which left the
  pointer short of where you moved it, by up to a hundred pixels after a drag
  let go while moving. The app now sends no pointer movement at all: it
  places the pointer itself, at a moment Windows waits for, so none of your
  movement is lost. A drag let go while moving still drops where you let go
  of the button.
- Windows: the pointer keeps up while the app is busy. With another part of
  the app at work, every mouse event on the computer could wait about 45 ms
  for the filter; it now waits a few milliseconds, because the app asks
  Windows for 1 ms timers while the filter is on.
- Windows: a right click made during a drag, while the pointer was catching
  up with your hand, now lands where you clicked, not where the pointer had
  been left.
- Windows: a click over an app running as administrator, made while the
  pointer was catching up, is no longer lost: it goes through as it came.
- Windows: a pen or touch tap made right after moving the mouse now lands
  where the pen touched, not where the mouse pointer was.
- **The settings controls added in 1.0 look like the rest of each system.**
  On macOS, a style setting meant only to keep the panes see-through also
  changed how every control inside them was drawn: the window values showed
  as boxed number fields, the button pickers drew a menu item inside the
  button, and **Add App…** was a dark pill. Each window value is now a
  pop-up button (macOS) or a drop-down list (Windows) of common values, with
  a calibrated value listed among them; the button pickers and the
  **Add App…** menu open the system's own menus on macOS; and every control
  is drawn natively on both systems, in light and dark appearance.
- Windows: the Test pad says "Click here", in sentence case like the rest of
  the Windows app.
- A Mac copy that updates itself from 1.0.0 is renamed to
  **Mouse Double-Click Fixer** in Applications on this update. One that
  updates straight from 0.x keeps its old name until the update after.

## 1.0.0 — 2026-10-10

- **DoubleClick Fixer is now Mouse Double-Click Fixer.** Your settings, the
  macOS permission and the open-at-login setting carry over. A Mac copy that
  updates itself to 1.0 keeps its old name in Applications until its next
  update.
- **Back and forward buttons can be filtered.** They are off until you turn
  them on. A press within the window of the last release is dropped with its
  release. Their releases are never held back for the window, and a drag
  made with one isn't protected. Calibrate and the Test pane cover them.
- **Each button has its own filter window.** Calibrate measures the button you
  pick, and Apply sets that button's window and starts filtering it. A new or
  uncalibrated install starts at 46 ms; it was 60. The window you had carries
  over to every button, except that a copy never calibrated and still on 60 ms
  moves to 46.
- **A scroll-wheel fix** for a worn encoder that reports one notch the wrong
  way. A notch that goes against the one before it, within the wheel window,
  is dropped. The window is 50 ms unless you change it, and can be 10 to 150
  ms. Vertical and horizontal scrolling are judged separately, no notch is
  held back for the window, and smooth scrolling from a Mac trackpad, a Magic
  Mouse or (on Windows) a precision touchpad is never touched. Off by default.
- **Apps:** a list of apps in which nothing is filtered while they are in
  front. The active app counts, not the window under the pointer.
- **Devices:** the mice the app has seen, each of which can be left
  unfiltered. Trackpads, touchscreens and pens are not filtered and have no
  switch (on Windows, precision touchpads only; see Windows below).
- **History:** for each button, bounces per 100 clicks over the last 30 days,
  a trend, and how long after a release each bounce came. The wheel fix's
  count is there too. It is kept in wear.json beside the settings, as daily
  counts only (with the filter window in use that day), for a year.
- Requires macOS 13 or later on Apple silicon, or 64-bit Windows 10 version
  1809 or later, or Windows 11.

### Filtering

- **Drags are protected from the first moment.** Every release is now held for
  the filter window, however short the click. A release 12–30 ms after its
  press used to go straight through, so a contact that bounced twice as it
  closed could turn the start of a drag into a click. Very short taps now wait
  like any other click. The price is that a release reaches apps later than it
  would unfiltered. A click made in place goes out sooner if the pointer
  leaves the spot first; otherwise a release goes out once the filter window
  has passed, and on a still mouse after the window plus the allowance below,
  which is 5 ms or more.
- **A small movement no longer ends a drag.** Since 0.5.1 on macOS, a release
  held in place was settled by any pointer motion, even a hand resting on the
  mouse, and a dropout's comeback could make a drag look like a click made in
  place. Now motion inside the filter window settles such a release only once
  the pointer has moved a few pixels from where the button came up.
- **A drag survives a busy computer.** A held release is now settled by the
  time of the events that follow it, not by a timer started when it arrived.
  Events reach the filter in the order they happened, so once one from after
  the filter window arrives, no press can still be on its way to cancel the
  release. Before, a dropout's comeback press that reached the filter late
  could find its release already sent, and the drag broke. With nothing after
  it, as on a still mouse, a timer settles the release after the window plus
  an allowance for how late this computer has lately delivered mouse button
  events, kept between 5 and 150 ms.
- A held release that comes due while the app is still re-sending other input
  now waits behind it, so apps can never see a button come up before it went
  down.
- **Input the app re-sends stays in order.** A click of one button no longer
  overtakes another button's release that is still on its way, which could
  show apps two buttons down together when they never were.
- On a slow computer a later click can no longer overtake a release the app
  re-sent. The app waits for it to come back for twice the worst delay it saw
  in the last two seconds, but not less than 150 ms or more than half a
  second; it used to stop waiting after 150 ms. If another program's hook
  swallows it, the app gives up after that wait and sends what was queued
  behind it.
- A click that passes untouched (from a trackpad, touchscreen or pen, from a
  device you left unfiltered, or while an app on your Apps list is in front)
  is never half filtered: its release goes the way its press went, and a
  release of the same button still held is sent first, so apps never see two
  presses in a row.
- Turning the filter off or quitting first sends everything held back or
  waiting, and waits up to a second for a send already under way, so nothing
  is left to go out once the filter has stopped.
- Clicks and pointer moves no longer wait up to 5 ms for the app's own window
  to finish what it is doing.

### macOS

- **Correction to 0.5.3.** 0.5.3 said the driver stamps clicks in
  nanoseconds. Real clicks, where the filter sees them, carry mach ticks;
  only clicks other programs post carry nanoseconds. So 0.5.3 still timed
  real clicks by when its own code ran, and a posted click seen first after
  the filter started could have put every later click 41.67 times too close
  together, so clicks seconds apart were dropped as bounce. Each event's
  timestamp is now read in whichever unit it carries.
- **A move that takes the pointer off the spot of a click can no longer reach
  apps ahead of the click's release.** Clicks and pointer motion now come
  through one event tap, which macOS hands over strictly in order. The
  separate motion tap 0.5.1 added took effect too late, so the first moves
  after a click could overtake it. The app now receives every pointer move,
  but looks at one only while it holds back a release or has input to re-send
  (where the pointer is, when it moved, and whether the hardware made it);
  every other move goes straight through.
- The scroll wheel is in the event tap only while the wheel fix is on, so with
  it off scroll events never reach the app. Turning the fix on or off replaces
  the tap: held releases are settled first, and the old tap stays until the
  new one has taken over and nothing the app re-sent can be lost with it.
  Scrolling that macOS calls continuous (a trackpad, a Magic Mouse) and
  scrolling another program posts are left alone.
- The app finds which mouse sent a click through IOKit, looking each device up
  once. Clicks macOS marks as coming from a tablet or a touch surface are
  never filtered. Button numbers 3 and 4 are the back and forward buttons;
  buttons past those pass untouched.
- Two separate clicks no longer become a double-click after a blocked
  bounce. macOS counts a suppressed press toward a double-click; the app now
  counts only the presses apps receive, by macOS's rule and your
  double-click speed.
- After a drag let go while moving, the pointer no longer stays behind where
  the button came up; it is put back where your hand has taken it.
- Removing the app's permission while it filters stops the filter within a
  second, and clicks go through untouched. macOS can keep reporting the app
  as allowed after it is removed from the list, so the app checks by asking
  for an event tap. It starts again by itself once allowed, and your "on" is
  kept.
- The permission list is named as System Settings names it: **Device
  Control and Data Access** on macOS 27 and later, **Accessibility** on 26
  and earlier. **Open Settings…** in General now always opens it.
- If macOS switches the filter off three times within 30 seconds, the filter
  stops and says so instead of fighting the system; turn it on again from
  the menu bar.
- The filter is rebuilt after sleep and when its tap is found switched off,
  pauses while your session is in the background with fast user switching,
  and releases its event tap when it stops (each stop used to leave one
  registered).
- The app is signed with the hardened runtime and clears the environment
  variables that would make the libraries inside it load code from
  elsewhere. It starts the programs it needs (pgrep, codesign, ditto, open,
  hdiutil, and bash for an update) by full path, with PATH set to the system
  folders and bash's start-up variables removed. So other programs can't use
  those routes to act with its permission. Its signing requirement is
  unchanged, so the permission carries over from 0.5.
- A Window menu with Minimize (⌘M) and Zoom, and the menu bar icon keeps the
  place you drag it to.
- `installer/uninstall_macos.sh` waits for the app to quit before it
  removes anything, and stops with a message if the app won't quit.

### Windows

- **Fixed: a click followed by a quick move was lost or became a drag.**
  This is the Windows fix 0.5.1 promised. While a release is held, the first
  move off the spot waits, the release goes out where you clicked, and the
  move follows. A drag let go while moving drops where the button came up.
  While the pointer is hidden (as it is in most games' mouse-look) the app
  never holds back or re-sends pointer movement. In that case and in a remote
  session, a held release goes out once the filter window has passed, with
  your next click or by a timer, wherever the pointer is then.
- Precision touchpads, touchscreens and pens are told apart from mice, through
  Raw Input and the marker Windows puts on pen and touch input, and are not
  filtered. Touchpads have three exceptions: a tap within about a second of
  using a mouse counts as that mouse's click; a touchpad that isn't a
  precision one reports itself to Windows as a mouse; and without Raw Input
  every touchpad click is filtered. Touchscreens and pens always pass.
- On Windows the wheel fix judges only a notch that a mouse reported through
  Raw Input, so a precision touchpad's scrolling is left alone.
- Clicks are timed on the precise clock as they arrive, not by Windows'
  15.6 ms tick, which was coarser than the gaps being judged. A bounce at
  the start of a drag is told apart again, a calibrated window near 30 ms no
  longer lets 20–30 ms chatter through, the Test pane and calibration show
  real gaps (they only ever showed 0, 15, 16 or 31 ms), and the first click
  after weeks without one is no longer swallowed.
- With the buttons swapped for left-handed use, a re-sent release now comes
  back as the same button; it came back as the other one.
- The mouse hook is re-installed every 15 seconds (was every minute) and
  whenever the session is unlocked or reconnected or the PC wakes, when
  Windows most often drops it.
- Re-sent input goes out from a thread of its own. Sending it from inside
  the hook could stall all input on the PC.
- One copy runs per user and session. With fast user switching, a second
  user's copy used to start unreachable, and their later launches showed
  nothing.
- The installer installs a folder, which starts without unpacking itself at
  every launch and sign-in; the portable exe stays one file. It needs
  64-bit Windows 10 version 1809 or later and says so instead of installing
  something that can't start. It closes a running copy through the installed
  one and never waits on it for more than 20 seconds.
- Ticking "Start Mouse Double-Click Fixer when I sign in" clears an earlier
  "off" in Task Manager's Startup apps; uninstalling removes both.
- Updates now finish in folders whose names use characters outside the
  system's code page (a user name like Łukasz on an English system).
  Before, the app didn't come back after updating there.
- The updater starts cmd.exe, and the Windows programs its update script
  runs, from System32 by full path. Named alone, Windows would look for them
  in the app's own folder or the current folder first, so a file with one of
  their names beside the portable exe (in Downloads, say) could have run
  instead.
- Windows builds no longer carry Mesa's software OpenGL (opengl32sw.dll,
  about 20 MB), which the app never used.
- Update checks and downloads go through Windows' own TLS (Schannel). The
  build no longer ships Qt's OpenSSL plugin and the copy of OpenSSL it picked
  up from another program on the build machine, nor a copy of the Universal
  C Runtime, which Windows 10 and later have built in and keep updated.

### Updates and security

- **Updates are signed.** A release's SHA256SUMS.txt carries a minisign
  (Ed25519) signature made with a release key that never goes to GitHub or
  CI. The app has two public keys built in, a primary and a backup. An
  update is offered only when the signature checks out, names exactly that
  version, and that version is newer than yours; one that fails is never
  downloaded, and General says why.
- Copies older than 1.0 trust checksums alone (on macOS, plus the
  code-signature check) for the update that brings them to 1.0. From 1.0
  on, every update is signature-checked.
- On macOS an update must also be the version it claims and keep the
  installed app's signing requirement, which the permission depends on. A
  move to a new signing certificate has to be named in a signed release.
- Pre-releases, which are for testing, are never signed, and installed copies
  never offer them: they look only at the latest full release.
- Releases are now built by CI as drafts. Before one is published, the
  maintainer's Mac checks every file against CI's build, checks that the
  macOS app's code signature is valid and that it loads no code named in its
  environment, and signs the checksums.
- Every build checks where each binary it collects comes from, and fails if
  one isn't from Python or its packages, so a library found on the build
  machine's PATH can't get in.
- The app now runs on Python 3.14 and ships its OpenSSL 3.5, a long-term
  support release maintained until April 2030. 0.5.3 and earlier were built
  on Python 3.13, whose OpenSSL 3.0 stopped getting security fixes on
  7 September 2026. On macOS this OpenSSL carries update checks and
  downloads; on Windows, where those go through Schannel, the app uses it
  for checksums. A build whose OpenSSL is out of support now fails its own
  self-test.
- Checking for updates and installing them have separate switches, so
  turning off automatic installs no longer stops the checks. If you turned
  updates off before 1.0, both stay off.
- A copy that can't replace itself says so before downloading anything, for
  example "Move Mouse Double-Click Fixer to Applications to update it."
- A version that failed to install twice is no longer retried at every
  sign-in; it waits for you. A download waiting to install is no longer
  fetched again by the next check, and "Update to X" in the menu opens
  General so progress and any failure show.
- Every build ships THIRD_PARTY_NOTICES.md, the licences of Qt, Python, the
  code inside them and the other software the app is built on, including
  the LGPL and GPL texts, worked out from the files that build ships.
  **General › Acknowledgements…** opens it, and on Windows it is also in the
  install folder. Each release also carries both platforms' copies, as
  THIRD_PARTY_NOTICES-macos.md and THIRD_PARTY_NOTICES-windows.md.

### App

- **Copy Diagnostics** (Windows: **Copy diagnostics**) in General copies
  the version, OS, the filter's state, the settings, the pointing devices
  the app has seen (by name) and the end of the app's log, for a bug report.
  The log records start-up, the filter starting and stopping, permission
  changes, waking from sleep and switching users, the TLS library in use,
  failures and updates, never clicks; hard crashes go to crash.log beside it.
- Your "on" survives failures. A filter that can't start at login, or stops
  on its own, used to switch itself off for good. Now the menu's status line
  says what happened, a start the app makes itself retries at 1, 3, 8 and
  20 seconds, and the menu item turns it off rather than trying again.
- Calibration starts with the first click on the pad (Begin is optional).
  Double-clicks count as pairs within your system's double-click speed, up
  to a second, and a pair that is too slow says so. The pad measures every
  button, and the Test pane shows all of them. Calibrate measures one button
  at a time, chosen at the top or by the Calibrate link beside it; a side
  button has no double-click, so it is measured by pressing it twice quickly.
  The Test pane's line is the window of the button last pressed.
- Filtering pauses only while Calibrate is measuring with its window in
  front. Reading the intro, switching to another app or leaving a result
  unapplied no longer leaves clicks unfiltered, and the menu can turn
  filtering on from the result screen.
- Open at login says when the system has it turned off (macOS Login Items,
  or Task Manager's Startup apps) and how to turn it back on. A copy run
  from the disk image no longer sets itself to open from there.
- Settings survive a crash mid-save and a file that can't be read: the last
  good copy is kept as settings.json.bak and used if settings.json is
  damaged. A settings.json that stays locked when the app starts (a backup
  tool or virus scanner holding it) is no longer written over with defaults:
  nothing is written until it can be read, and then what you changed meanwhile
  is laid over what it holds. wear.json is saved and protected the same way.
- Going back to 0.5.3 works, with two losses. It has one window for every
  button and takes the left button's. It doesn't know the back and forward
  buttons, so it drops them from the filtered buttons the next time it saves.
  1.0 reads the rest of its settings again.
- Switches read as named checkboxes and the sidebar as a list to VoiceOver
  and Narrator, and keyboard focus is shown.
- The first window fits the screen, and the Windows "still running"
  notification shows once rather than at every sign-in.

## 0.5.3 — 2026-10-08

- macOS: the filter now measures click timing from the driver's own
  timestamps. It read them in the wrong unit before (as mach ticks; they are
  nanoseconds), so it never trusted them and timed clicks by when its own
  code ran instead, which is noisier.

## 0.5.2 — 2026-10-08

- Windows: the installer and uninstaller can now close a copy of the app that
  is still starting up. The exe unpacks itself for a few seconds before it
  listens, and a quit request in that moment used to be missed, leaving the
  old copy running with files in use.

## 0.5.1 — 2026-10-08

- **Fixed: clicks lost when you moved the mouse right after clicking.** The
  filter holds every release for the filter window (to tell a contact dropout
  from a real release), and it then re-sent the release at wherever the pointer
  was by then. A click followed by a quick move landed off the button or link,
  so it never registered. Re-sent releases now keep the place you actually let
  go, and for a click made in place the first pointer movement delivers the
  release at once, before the movement reaches apps. Drags keep their
  dropout protection.
- Fixed: a press re-ordered behind a late release was counted against the
  double-click count, so the next press could be read as a single click.
- Re-sent events that never come back are given up on after 150 ms, not 500.
- Windows: this release keeps the previous behaviour for the mouse-move case;
  a Windows fix follows.

## 0.5.0 — 2026-10-01

One version for both platforms, and the release that rolls up everything since
0.2.0. If you are on any 0.2 release, this is the one to install.

- **Filtering:** blocks the extra click a worn switch adds, measured as the gap
  between a release and the next press, so real double-clicks are untouched.
  Drags survive contact dropouts, including bounce at the moment you press and
  several dropouts in quick succession, and re-sent clicks keep their real
  time and order.
- **Native on both platforms:** a System Settings-style window and menu bar
  item on macOS, with a layered Liquid Glass icon on macOS 26 and later; a
  Windows 11 Settings-style window and notification area icon on Windows.
- **Calibration and testing:** a guided calibration that recommends a filter
  window, and a Test pane that shows the bounces the filter blocks.
- **Updates:** automatic, checksum-verified, signature-checked on macOS, and
  they wait while the window is open.
- **Windows:** the installer and uninstaller close a running copy, even one
  still starting up; the exe now carries its version (Properties › Details).
- Requires macOS 13 or later (Apple silicon), or Windows 10 or 11.

## 0.2.12 — 2026-10-01

- Release pages now show what changed in each version, taken from this
  changelog, and older ones point to the latest release.
- Updated documentation: updates that wait for the window to close, apps
  running as administrator on Windows, and the hook re-arming itself.
- No change to how the app works.

## 0.2.11 — 2026-10-01

- A press made just as the app re-sends a held release can no longer overtake
  it (apps saw the button go down twice, which could break a double-click).
  Re-sent events are tracked until they pass back through the hook, and real
  events wait behind them; a lost one is given up on after half a second.
- Windows: --quit, used by the installer and uninstaller, now also closes a
  copy that is still starting up, and waits until it has exited. Opening the
  app twice in quick succession no longer runs two copies.

## 0.2.10 — 2026-10-01

- macOS: the menu bar icon is drawn at the size and weight of Apple's own menu
  bar icons (Wi-Fi, Sound); it was smaller and thinner than its neighbours.
- Windows: a new notification area icon that fills the tray square, keeps
  crisp strokes at 16 px and shows the button split in both states (an outline
  when off, solid when on); the old one was small and the "on" state was a
  plain blob.

## 0.2.9 — 2026-10-01

- When GitHub has no release the updater can read (none published yet, or the
  repository is private), Software Update now says "No published releases were
  found" instead of "Up to date".
- CI now installs 0.2.6, leaves it running, upgrades over it, quits with
  --quit, runs a full in-app update and uninstalls, on a real Windows machine.

## 0.2.8 — 2026-10-01

- macOS 26 and later: a layered Liquid Glass app icon (Icon Composer), so the
  system can draw its light, dark, tinted and clear looks.
- Requires macOS 13 or later, which the bundled Qt has needed all along; older
  macOS now gets a clear message instead of a crash at launch.
- Windows: the mouse hook no longer asks a busy window to hit-test itself (that
  could stall long enough for Windows to drop the hook), and it re-installs
  itself after a slow moment and every minute, so filtering can't silently stop.
- A contact that bounces as you press no longer turns the start of a drag into
  a click.
- Re-sent clicks keep their real time, so the Test pane no longer paints
  double-clicks red while the filter is on, and it now shows the bounces the
  filter blocked.
- macOS: a triple-click with a bounce in it stays a triple-click.
- Turning the filter on from the menu during calibration waits until
  calibration ends; the menus say "Paused for calibration" or "Waiting for
  Accessibility access" instead of "Off".
- Revoking and re-granting Accessibility brings the filter back by itself, even
  after a restart; turning it off while it waits is remembered.
- Calibrate: no Finish button that could only fail; a run without bounce
  explains itself; the Bounce Filter pane suggests calibrating until you have.
- Updates: a background update waits while the window is open and installs
  when you close it; a full disk or failed Windows install no longer leaves the
  app stuck or closed; after an update the General pane says how it went;
  releases still uploading are skipped.
- Windows installer: an update no longer turns "Open at login" back on, and it
  closes copies older than 0.2.7 too. Windows wording uses sentence case.
- Windows: reopening the app brings its window to the front; a maximized
  window stays maximized; the tray icon follows a taskbar-only theme change.
- The tests no longer quit a copy of the app that is running.

## 0.2.7 — 2026-09-29

- Windows: clicking in an app running as administrator (Task Manager, an
  admin terminal) no longer leaves the button stuck down. Windows blocks
  re-sent input to such windows, so releases there go straight through.
- A switch that drops out twice in quick succession no longer ends the drag:
  each held release now gets its own full filter window.
- Windows: a click right after the 49.7-day tick-count wrap is no longer
  mistaken for bounce, and event times line up on every Python version.
- macOS: an unexpected error in the event tap passes the click through
  instead of dropping it.
- Opening the app always shows its window. The "Start in menu bar" switch,
  which hid it even when opened by hand, is gone; login launches stay quiet.
- The Windows installer and uninstaller close a running copy first, so no
  file is left in use.
- Windows: the notification area icon is a monochrome glyph that follows
  the taskbar theme, visible on the default dark taskbar.
- Calibration and the Test pane time clicks from the events themselves.
- The filter window is saved when the slider is released, not at every step.
- The Test pane says when the filter is hiding bounces from it.
- macOS: Login Items lists the app under its own name and icon.

## 0.2.6 — 2026-09-26

- Windows: the window follows Windows 11 Settings more closely. Toggles show
  On or Off, every setting card has a Fluent icon, the main action is an
  accent button, and without Mica the title bar matches the window.
- The tray icon is only redrawn when the filter turns on or off.

## 0.2.5 — 2026-09-26

- Windows: minimizing the window sends it to the notification area, like
  closing, instead of leaving a button on the taskbar.

## 0.2.4 — 2026-09-25

- Drags are consistent. 0.2.3 only protected a drag once the button had been
  held for 120 ms, but worn switches drop contact as early as 60 ms in, which
  still ended the drag. Releases are now held once the button has been down
  for 30 ms; a bouncy click comes through as one clean click.
- macOS: a cancelled dropout no longer makes the next click count as a
  double-click, and re-sent events carry the corrected click count.

## 0.2.3 — 2026-09-24

- Dragging survives a worn switch. While the button is held, a contact
  dropout used to end the drag and swallow the re-press; releases after a
  long hold are now held for the filter window and dropped together with a
  press that follows, so the drag carries on. Ordinary clicks gain no delay.
- macOS: clicking the menu bar icon no longer quits the app. Qt's tray icon
  crashes on macOS 27 (fixed upstream after Qt 6.11.2), so the menu bar item
  is now built on NSStatusItem, and uses the SF Symbols mouse glyph.
- Closing the window on the Calibrate pane no longer leaves filtering paused.
- The blocked-bounce count is no longer rolled back by settings changes, and
  the menu bar shows it live.
- A hook that stops on its own now switches the filter off everywhere instead
  of leaving the menu bar showing "On".
- A real double-click is no longer seen as a triple-click on mouse-up after a
  blocked bounce (macOS).
- One error dialog per failure, not two; a failed login-item change restores
  the previous state; background launches no longer raise the permission
  prompt with no window on screen.
- Updates: stalled downloads time out, errors surface instead of hanging, and
  downloads are cleaned up afterwards. Windows: the portable updater retries
  the file swap, no console windows flash, silent updates return to the
  notification area, and the installer's startup option matches the app's.
- Installer cleanup compares file identity rather than access time; the build
  script no longer passes an empty keychain to codesign.

## 0.2.2 — 2026-09-23

- macOS: clicking the menu bar icon opens its menu again. In 0.2.1 the window
  opened on any activation, including the one a menu bar click causes, which
  dismissed the menu. The window now opens on the reopen event macOS sends
  when a running app is opened from Launchpad, Spotlight, Finder or the Dock.

## 0.2.1 — 2026-09-22

- macOS: DoubleClick Fixer runs as a menu bar app. It has no Dock icon while
  its window is closed; opening the window brings back the Dock icon and app
  menu, and opening the app from Launchpad or Spotlight shows the window.
- macOS: the installer disk image is ejected once the installed app launches,
  and moved to the Trash if it is in Downloads.

## 0.2.0 — 2026-09-19

### Changed

- **Filtering now measures the gap between a release and the next press**
  instead of the time between two presses. That is the signature of switch
  bounce and the measurement mouse firmware uses, so the filter no longer has
  to trade off against normal double-click speed. Thresholds from 0.1 do not
  carry over; recalibrate after upgrading.
- **New interface**, rebuilt on Qt: an overview with live bounce statistics and
  a gap timeline, a guided two-step calibration, and a settings page. Follows
  the system light/dark theme.
- **Native tray icon** on both platforms, replacing the previous tray library,
  which could crash when paired with Tk on macOS.
- Calibration pauses the system-wide filter automatically so it measures the
  real mouse, and no longer blocks the fix behind an incomplete run.
- Settings moved to the platform's config directory and gained a schema
  version; 0.1 settings are migrated.

### Added

- Automatic updates from GitHub Releases, verified by checksum and, on macOS,
  by code signature; the app installs the update and reopens by itself.
  Releases are signed with a stable certificate so the Accessibility
  permission carries over to every update.
- The Accessibility request goes through macOS itself, so the entry in System
  Settings always matches the installed copy.
- A designed disk image window, and an app icon for both platforms.
- The window reopens at its last size and position.
- Right and middle buttons can be protected as well as the left one.
- Start hidden in the tray, in addition to start at login.
- Single-instance launch: opening the app again reveals the running window.
- macOS Accessibility status is shown in-app, with a link to the settings pane.

### Fixed

- The window's close handler was defined twice, so closing always quit the app
  and skipped saving settings.
- Calibration could end in a state where the fix stayed permanently disabled.
- macOS: an event tap disabled by timeout or by sleep is now re-armed instead
  of silently dying, and the click count of a suppressed bounce is repaired so
  a real double-click is not reported to apps as a triple-click.
- Windows: injected (software-generated) clicks are passed through untouched,
  and event timestamps come from the OS rather than from when Python woke up.
- Statistics are no longer written to disk from inside the hook callback, which
  Windows can drop for taking too long.
- The Windows installer script declared the startup registry value in a section
  that does not accept one, so the installer never compiled. The release build
  can now be run on demand to catch this without cutting a tag.

## 0.1.0

- First cross-platform release.
