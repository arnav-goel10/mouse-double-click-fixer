# Changelog

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
