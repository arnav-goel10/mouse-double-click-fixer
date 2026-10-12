# Getting started

Names are written as macOS shows them. Windows uses sentence case for the same
labels (**Test and tune**, **Copy diagnostics**), calls its first page
**Home** where macOS says **General**, and its last page **Settings** where
macOS says **About**.

## 1. Open it

On macOS, the first window explains why the app needs permission. Choose
**Continue**, then allow Mouse Double-Click Fixer when macOS asks (see
[Installation](INSTALLATION.md#macos)). The window says "Waiting for
permission…", moves on by itself to **Bounce Filter is on**, and offers
**Test and Tune…**.

On Windows, the window opens on **Home**, with a note about pinning the icon
to the taskbar corner so it stays visible, and a **Test and tune** button.

## 2. Test and Tune

Open **Test and Tune…** from the mouse icon in the menu bar (macOS), from
**General**, or from the Dock icon's menu; on Windows, choose **Test and
tune** on **Home**. It is one task in three steps. It tests the left button
unless you pick another at the top of the first step.

**Step 1: click the pad.** Your first click on the pad starts the test, and
counts.

- **Single clicks:** click once, pause, and repeat until twelve are counted.
  Any extra press the switch adds is recorded as bounce, and the pad tells
  you ("Extra click caught (12 ms after letting go)").
- **Double-clicks:** double-click five times at your usual speed. This sets
  the limit the filter must stay under. A double-click counts when its second
  click comes within your system's double-click speed, up to a second; a
  slower pair says it was too slow. For back and forward, which have no
  double-click, press twice quickly instead, as when going back two pages.

While it measures, filtering pauses so the pad sees your mouse as it is. It
pauses only while the first step is measuring and its window is in front;
switch to another app and filtering carries on. The next steps filter as
usual.

**Step 2: the verdict.** In plain words, one of:

- "Your left button double-clicks on its own", with how many extra clicks came
  in how many clicks, and that each came within some number of milliseconds of
  letting go;
- "No bounce seen": your mouse may be fine, or it bounces only now and then.
  A light setting catches it when it does.

Under it are your fastest double-click, in milliseconds between release and
press; **In everyday use**, what the filter has caught in the last 30 days
(the same health check as on **History**); and a line for any other mouse
utility that is running (see [Other mouse utilities](#other-mouse-utilities)).
Choose **Continue**, or **Test Again**.

**Step 3: tune the button.** A pop-up lists windows in milliseconds, with one
marked "(recommended)". The recommendation aims above the worst bounce the pad
saw, with some margin, but never higher than half of your fastest
double-click, so your real double-clicks still count. When the two collide it
keeps to half your fastest double-click and says there is little room. With no
bounce seen it is 46 ms, or half your fastest double-click if that is
smaller. Under the pop-up the app says whether your fastest double-click still
counts at the window you chose ("Too long" if it wouldn't), and a second pad
lets you try it: a bounce is marked as ignored, a deliberate double-click as
counted. Choose **Apply**.

Applying sets that button's window, marks the button **Calibrated** on
**General** (**Home**), and starts filtering that button, since it was worth
measuring. The last screen says the button is tuned. It has **Test Another
Button**, and a **Turn On** button if **Bounce Filter** is still off. Closing
the window before you apply starts the test over next time. Repeat for any
other button that bounces.

## 3. Check that it is on

The status line says what is happening. In the macOS menu bar menu it reads
"Filtering · 1,981 bounces blocked"; on **General** or **Home**, and in the
Windows tray flyout, it is a sentence such as "No extra clicks seen yet." or
"Your left button bounced 14 times today. Every bounce was caught." If a
button's bounces come close to getting past its window, it says "Some came
close to getting past: see History."

The **Bounce Filter** switch at the top of **General** (**Bounce filter** on
**Home**) turns it on and off. The macOS menu bar menu has **Filter Bounce**
with a checkmark; the Windows tray flyout has the switch.

Under **Buttons**, tick each button that clicks twice on its own. The left
button is on by default; right, middle, back and forward are off until you
turn them on. Back and forward use the click rule only: a press within the
window of the last release is dropped, and their releases are never held.

Try it: single-click a file. It should stay selected instead of opening, while
a deliberate double-click still opens it. Drag something across the screen;
the drag should hold all the way.

## 4. Strength

**Strength** has two settings. **Automatic** uses what Test and Tune measured
for each button, or 46 ms for a button it hasn't measured. **Custom** shows a
window for each filtered button, in milliseconds, for you to set. On Windows
the windows sit inside the **Strength** card, which expands, and can be
changed once you choose Custom. Each setting keeps its own windows, so
switching between them loses nothing.

Most people never need Custom. See
[Choosing a filter window](#choosing-a-filter-window) if you do.

## 5. Leave it running

Close the window and the app keeps filtering from the menu bar (macOS) or the
notification area (Windows).

- **macOS:** click the mouse icon for the menu: the status line, **Filter
  Bounce**, **Pause for 15 Minutes**, **Test and Tune…**, **Settings…** (⌘,)
  and **Quit Mouse Double-Click Fixer**. The Dock icon, shown while Settings
  is open, has the same actions. If you hide the menu bar icon with **General
  › Show in menu bar**, or macOS hides it, the Dock icon stays, and opening
  the app again opens Settings.
- **Windows:** left-click the icon for a flyout with the status, the **Bounce
  filter** switch and **Open**. Right-click it for **Open**, **Pause for 15
  minutes**, **Start with Windows** and **Exit**. The icon may sit under the
  **^** overflow arrow until you drag it next to the clock.

To make it start by itself, turn on **General › Open at login** (macOS) or
**Start with Windows** (the tray menu, or **Settings › Startup**). At login it
starts quietly, with no window. If the system has the app turned off in its
own list of login items, the setting says so and how to turn it back on.

## 6. Pause it

**Pause for 15 Minutes** in the menu (**Pause for 15 minutes** on Windows)
stops filtering for 15 minutes. The menu then says **Resume**, and the status
line says "Paused until 3:45 PM". The same pause is on a shortcut: ⌃⌥⌘P on
macOS, Ctrl+Alt+Shift+P on Windows. Press it again to resume. The shortcut
reads no other key: the system tells the app only when that combination is
pressed. Turn it off under **General › Pause shortcut** (macOS) or **Settings
› Pausing** (Windows).

A pause is not saved. If you quit the app or restart the computer during one,
filtering is back on when the app next starts. Turning the filter off ends a
pause. There is nothing to pause while the filter is off, so the menu item is
greyed out (macOS) or missing (Windows) and the shortcut does nothing.

To pause automatically in full-screen games, see [Games](#games).

## 7. The other panes

- **Fix wheel reversals**, under **Other Fixes** on **General** (**Other
  fixes** on **Home**): for a wheel that now and then jumps one notch the
  wrong way. Off by default. With it on, a notch that goes against the one
  before it within the wheel window (50 ms by default) is dropped. Smooth
  scrolling is never touched: a Mac trackpad's, and on Windows a precision
  touchpad's.
- **Exceptions** has three parts:
  - **Apps** lists the apps in which nothing is filtered while they are in
    front, such as a game. In front means the active app, the one with the
    keyboard focus, not the window under the pointer.
  - **Games** has one switch, **Pause during full-screen games**; see
    [Games](#games).
  - **Devices** lists the mice the app has seen, each with a switch, for a
    second mouse you don't want filtered. Trackpads, touchscreens and pens
    are listed separately as "Not filtered", with no switch. On Windows that
    means precision touchpads; see
    [Troubleshooting](TROUBLESHOOTING.md#devices) for the exceptions.
- **History** shows the health check, then, for each button, bounces per 100
  clicks over the last 30 days, a trend, and how long after a release each
  bounce came. See [History](#history).
- **About** (macOS) or **Settings** (Windows) has updates, **Copy
  Diagnostics**, the blocked-bounce count with **Reset…**, a privacy note and
  **Acknowledgements…**. On macOS, the permission's status is there too.

### Games

Under **Exceptions › Games**, **Pause during full-screen games** is off by
default. While it is on, the app asks every two seconds whether a full-screen
game is in front; if so, filtering stops and the status line says "Paused
while the game is in front" (with the game's name where there is one), until
the game leaves the front or you choose **Resume**. After **Resume** it won't
pause for that game again until the game has left the front.

- **macOS:** the app in front declares in its own bundle that it is a game (a
  Games category, or a request for Game Mode) and one of its windows covers a
  whole display. It doesn't check whether macOS's Game Mode is on; macOS gives
  no way to ask. A game whose bundle declares neither, or whose full-screen
  window belongs to another process, isn't seen.
- **Windows:** a full-screen exclusive Direct3D app, or a window that covers
  its whole monitor and either Windows says a full-screen app is running or
  the program is on the list of games Windows keeps for Game Bar. Browsers,
  video players, slide shows, meetings, remote desktop and virtual machine
  windows, and Windows' own screens never count. A game in a browser isn't
  seen, and another full-screen program that isn't a game may be taken for
  one.

This isn't a promise that a game's anti-cheat accepts the app; see
[Troubleshooting](TROUBLESHOOTING.md#games).

### History

At the top, one verdict about your mouse:

- "Your left button double-clicks on its own: caught 14 of 14": the filter
  ignored the extra clicks of the last 30 days. The count is of bounces the
  filter caught. A bounce that gets past the window looks like an ordinary
  click, so the app can't count it, and it watches for the signs of one
  instead (see the suggestion below).
- "No bounce seen": your mouse may be fine, or it bounces only now and then.
  ("No bounce seen yet" until the filter has counted some clicks.)
- "Another app may be changing clicks: Logi Options+": no bounce was caught,
  and another utility that can take over buttons or the wheel is running.

When a button's last 14 days of bounces, at least 20 of them, come close to
its window (the longest 1 in 100 reaches past 80% of it), History also
suggests a longer window: "Raise the left button to 64 ms?", with **Apply**.
The suggested window puts those bounces at 70% of it. It never goes above
half of your fastest double-click as Test and Tune measured it (60 ms if it
hasn't measured one), and nothing is offered that would raise the window by
less than 3 ms. It only ever raises a window, and changes nothing until you
choose **Apply**.

The trend needs at least 200 clicks in each 15-day half of the 30 days, so it
can't appear before you have used the app for 16 days.

### Other mouse utilities

If Logi Options+, Logitech Options, Logitech G HUB or Razer Synapse (both
systems), Mac Mouse Fix, SteerMouse, LinearMouse or BetterMouse (macOS), or
X-Mouse Button Control or DoubleClickFix (Windows) is running, **General**
(**Home**) says, for example, "Logi Options+ is running" and what it may take over: the side
buttons, the middle button, the wheel, or clicks. That is what the utility can
be set to do, not what it does on your computer. The app changes nothing in
it; if side buttons or the wheel act up, check its settings. The app looks
every minute and whenever you bring it forward, and it reads only the names of
running programs.

## Choosing a filter window

For **Custom**.

| Window | Effect |
| --- | --- |
| 20–40 ms | Catches typical bounce; unlikely to touch a double-click. |
| 40–80 ms | Catches severe bounce; fine unless you double-click very fast. |
| 80 ms + | Only for a badly worn switch; fast double-clicks may be dropped. |

Every release of the left, right and middle buttons waits for the filter
window before apps see it, a little longer on a still mouse, and a click made
in place goes out as soon as you move the pointer off it. So a shorter window
also means a quicker click. Presses go straight through; one waits only
behind a release still on its way to apps, usually for a millisecond or two.
Back and forward releases aren't held for the window, though like a press
they can wait a millisecond or two behind a release still on its way. The
scroll wheel is never held.

On Windows there is one exception to the pointer-off rule. While the pointer is
hidden (as it is in most games' mouse-look), and in a remote session, moving
the pointer doesn't release a click: it goes out once the window has passed,
with your next click or by a timer, wherever the pointer is then.
