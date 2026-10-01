# Changelog

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
