# First-run demo

## Calibrate

1. Open DoubleClick Fixer. The fix is off during setup.
2. In **Isolated clicks**, click the test area once, wait, and repeat until `10/10`.
3. Select **Next: double-clicks**.
4. Perform at least three natural double-click pairs. Extra pairs improve the estimate.
5. Select **Apply calibration**.

Calibration learns from two labeled behaviors. It does not treat every fast click as an intentional double-click.

## Verify diagnosis

Use the test area and watch **Clicks**, **Double-clicks**, and **Last interval**. This local test does not affect other applications.

## Verify the fix

1. Select **Enable fix**.
2. Confirm the status changes to `FIX ON`.
3. Click once on a file in File Explorer, then click again quickly.
4. A legitimate normal double-click should continue to work; only the learned bounce range is filtered.
5. The status changes to `FIX ON - FILTERED` when a duplicate is intercepted.
6. Select **Disable fix** to restore normal system behavior.

## Tray behavior

Clicking the window close button hides the app to the tray and preserves settings. Use **Quit** from the tray/menu bar to stop the hook and exit fully.
