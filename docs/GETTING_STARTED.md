# Getting started

## 1. See the fault

Open DoubleClick Fixer and choose **Test** in the sidebar. Click the pad the
way you normally would. Each bar is the pause between releasing the button
and pressing it again. A worn switch produces occasional tiny red bars: presses
you never made.

## 2. Calibrate

Choose **Calibrate**. Filtering pauses while you calibrate, so the raw mouse is
measured.

- **Single clicks:** click once, pause, and repeat until twelve are counted.
  Any extra press the switch adds is recorded as bounce.
- **Double-clicks:** double-click five times at your usual speed. This sets
  the limit the filter must stay under.

The result shows the recommended filter window, the longest bounce measured
and your fastest double-click. Choose **Apply**.

## 3. Turn it on

On the **Bounce Filter** pane, turn on the switch next to the app icon. The
**Activity** section, and the menu bar or tray menu, count the bounces blocked.

Try it: single-click a file. It should stay selected instead of opening, while
a deliberate double-click still opens it. Drag something across the screen;
the drag should hold all the way.

## 4. Leave it running

Close the window and the app keeps filtering from the menu bar (macOS) or the
notification area (Windows). Under **General**, turn on **Open at login** to
make that automatic: at login it starts there quietly, with no window.

## Choosing a filter window

| Window | Effect |
| --- | --- |
| 20–40 ms | Catches typical bounce; no risk to double-clicks. |
| 40–80 ms | Catches severe bounce; fine unless you double-click very fast. |
| 80 ms + | Only for a badly worn switch; fast double-clicks may be dropped. |
