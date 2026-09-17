# Troubleshooting

## The fix button is disabled

Calibration is incomplete. Finish the isolated-click phase and at least three intentional double-click pairs first.

## The status stays `FIX ON` but nothing is filtered

Use the latest release executable. Click once outside the app and confirm the status changes to `FIX ON - ACTIVE`. If it does not, disable and re-enable the fix. On macOS, verify Accessibility permission.

## Legitimate double-clicks are blocked

Lower the Bounce filter value, then select **Apply**. Values around 80-120 ms are common, but the correct value depends on the mouse switch.

## Settings do not appear after reopening

Use the tray/menu-bar **Quit** action or the window close button from the latest build. Settings are stored in:

- Windows/macOS: `~/.doubleclick-fixer.json`

## The app is hidden

Open it from the notification area on Windows or menu bar on macOS. The window close button hides rather than exits.

## macOS cannot enable the fix

Open **System Settings > Privacy & Security > Accessibility**, allow DoubleClick Fixer, then restart the app.

## Collecting a bug report

Include the app version, operating system version, mouse model, calibration threshold, and steps to reproduce. Never include the settings file if it contains information you do not want to share.
