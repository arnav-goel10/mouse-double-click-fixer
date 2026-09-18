# Troubleshooting

## Bounce still gets through

Raise the filter window on the **Bounce Filter** pane by 10–20 ms and test again on the **Test**
pad. Chatter is intermittent, so a calibration run can miss the worst of it.
Watch the bars: anything below the dashed line would be filtered.

## A real double-click was swallowed

Lower the filter. If you double-click unusually fast, calibration caps the
suggestion at half your fastest measured gap, but you can always set the value
by hand. Below 40 ms the filter is effectively free of that risk.

## macOS: the filter will not turn on

macOS only allows an event tap that can block events for a trusted app. Open
**System Settings > Privacy & Security > Accessibility**, add DoubleClick Fixer
and switch it on, then try again. Rebuilding the app from source changes its
signature, so macOS treats it as a new app and permission has to be granted
again — remove the old entry with the minus button first.

## macOS: it worked, then stopped

macOS disables an event tap that stalls, and after waking from sleep. The app
re-arms the tap automatically. If the menu bar icon still shows the filter on
but nothing is blocked, toggle it off and on.

## Windows: nothing is filtered in one specific app

A low-level hook cannot filter input for a window running at a higher privilege
level than DoubleClick Fixer. Run it as administrator if you need the filter
inside an elevated application.

## The window disappeared

Closing the window hides it; the filter keeps running. Reopen it from the menu
bar (macOS) or the notification area (Windows) — on Windows it may be under the
"^" overflow arrow. **Quit** there stops the filter and exits.

## Settings

Stored at `%APPDATA%\DoubleClickFixer\settings.json` on Windows and
`~/Library/Application Support/DoubleClickFixer/settings.json` on macOS.
Delete the file to start over. Settings from version 0.1 are migrated, except
the old threshold: it measured press-to-press time, which is a different
quantity, so recalibrate after upgrading.

## Reporting a bug

Include the app version (bottom of **General**), your OS version, the mouse model,
your filter window, and what the **Test** pane shows when the fault happens.
