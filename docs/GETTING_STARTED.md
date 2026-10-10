# Getting started

## 1. See the fault

Open Mouse Double-Click Fixer and choose **Test** in the sidebar. Click the
pad the way you normally would, with any button. Each bar is the pause between
releasing a button and pressing it again. A worn switch produces occasional
tiny red bars: presses you never made.

## 2. Calibrate

Choose **Calibrate**, then **Begin**, or just start clicking on the pad: the
first click counts. Calibrate measures one button at a time. It starts with
the left button; pick another at the top, or use the **Calibrate** link beside
a button on the **Bounce Filter** pane. While you click, filtering pauses so
the pad sees your mouse as it is. It pauses only while the Calibrate pane is
measuring with its window in front; switch to another app and filtering
carries on.

- **Single clicks:** click once, pause, and repeat until twelve are counted.
  Any extra press the switch adds is recorded as bounce.
- **Double-clicks:** double-click five times at your usual speed. This sets
  the limit the filter must stay under. A double-click counts when its second
  click comes within your system's double-click speed, up to a second; a
  slower pair says it was too slow. For back and forward, which have no
  double-click, press twice quickly instead, as when going back two pages.

The result shows the recommended filter window, the longest bounce measured
and your fastest double-click. Choose **Apply**: it sets that button's window
and turns that button on. Closing the window before you apply starts the
calibration over next time. Repeat for any other button that bounces.

## 3. Turn it on

On the **Bounce Filter** pane, turn on the switch next to the app icon. On
macOS, allow the app when macOS asks (see
[Installation](INSTALLATION.md#macos)). The **Activity** section, and the menu
bar or notification area menu, count the bounces blocked.

Under **Buttons**, each button has its own switch and its own window. The left
button is on by default; right, middle, back and forward are off until you
turn them on. A new or uncalibrated window is 46 ms. Back and forward use the
click rule only: a press within the window of the last release is dropped, and
their releases are never held.

Try it: single-click a file. It should stay selected instead of opening, while
a deliberate double-click still opens it. Drag something across the screen;
the drag should hold all the way.

## 4. Leave it running

Close the window and the app keeps filtering from the menu bar (macOS) or the
notification area (Windows). Under **General**, turn on **Open at login** to
make that automatic: at login it starts there quietly, with no window. If the
system has the app turned off in its own list of login items, **General**
says so and how to turn it back on.

## 5. The other panes

- **Scroll wheel**, on **Bounce Filter**: for a wheel that now and then jumps
  one notch the wrong way. Off by default. With it on, a notch that goes
  against the one before it within the wheel window (50 ms by default) is
  dropped. Smooth scrolling is never touched: a Mac trackpad's, and on Windows
  a precision touchpad's.
- **History** shows, for each button, bounces per 100 clicks over the last 30
  days, a trend, and how long after a release each bounce came. A rising line
  is a switch wearing out. The trend needs at least 200 clicks in each 15-day
  half of the 30 days, so it can't appear before you have used the app for 16
  days.
- **Apps** lists the apps in which nothing is filtered while they are in
  front, such as a game. In front means the active app, the one with the
  keyboard focus, not the window under the pointer.
- **Devices** lists the mice the app has seen, each with a switch, for a
  second mouse you don't want filtered. Trackpads, touchscreens and pens are
  listed separately, with no switch. On Windows that means precision
  touchpads; see [Troubleshooting](TROUBLESHOOTING.md#devices) for the
  exceptions.

## Choosing a filter window

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
