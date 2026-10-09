# Getting started

## 1. See the fault

Open DoubleClick Fixer and choose **Test** in the sidebar. Click the pad the
way you normally would, with any button. Each bar is the pause between
releasing a button and pressing it again. A worn switch produces occasional
tiny red bars: presses you never made.

## 2. Calibrate

Choose **Calibrate**, then **Begin**, or just start clicking on the pad: the
first click counts. While you click, filtering pauses so the pad sees your
mouse as it is. It pauses only while the Calibrate pane is measuring with its
window in front; switch to another app and filtering carries on.

- **Single clicks:** click once, pause, and repeat until twelve are counted.
  Any extra press the switch adds is recorded as bounce.
- **Double-clicks:** double-click five times at your usual speed. This sets
  the limit the filter must stay under. A double-click counts when its second
  click comes within your system's double-click speed, up to a second; a
  slower pair says it was too slow.

The result shows the recommended filter window, the longest bounce measured
and your fastest double-click. Choose **Apply**. Closing the window before
you apply starts the calibration over next time.

## 3. Turn it on

On the **Bounce Filter** pane, turn on the switch next to the app icon. On
macOS, allow the app when macOS asks (see
[Installation](INSTALLATION.md#macos)). The **Activity** section, and the menu
bar or notification area menu, count the bounces blocked. Under **Buttons**,
choose which buttons to filter; the left one is on by default.

Try it: single-click a file. It should stay selected instead of opening, while
a deliberate double-click still opens it. Drag something across the screen;
the drag should hold all the way.

## 4. Leave it running

Close the window and the app keeps filtering from the menu bar (macOS) or the
notification area (Windows). Under **General**, turn on **Open at login** to
make that automatic: at login it starts there quietly, with no window. If the
system has the app turned off in its own list of login items, **General**
says so and how to turn it back on.

## Choosing a filter window

| Window | Effect |
| --- | --- |
| 20–40 ms | Catches typical bounce; no risk to double-clicks. |
| 40–80 ms | Catches severe bounce; fine unless you double-click very fast. |
| 80 ms + | Only for a badly worn switch; fast double-clicks may be dropped. |

Every release waits for the filter window before apps see it, a little
longer on a still mouse, and a click made in place goes out as soon as you
move the pointer off it. So a shorter window also means a quicker click.
Presses go straight through; one waits only behind a release still on its
way to apps, usually for a millisecond or two.
