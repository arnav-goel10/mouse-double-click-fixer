# Changelog

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
