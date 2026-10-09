#!/usr/bin/env bash
set -euo pipefail

# Quit a running copy first, and wait until it has gone, so it can't recreate
# its settings on the way out or hold its permission while it is reset.
running() { /usr/bin/pgrep -x DoubleClickFixer >/dev/null 2>&1; }
wait_gone() {  # up to $1 tenths of a second
  for _ in $(/usr/bin/seq "$1"); do running || return 0; /bin/sleep 0.1; done
  ! running
}
if running; then
  /usr/bin/osascript -e 'tell application id "com.doubleclickfixer.app" to quit' 2>/dev/null || true
  wait_gone 100 || { /usr/bin/pkill -x DoubleClickFixer 2>/dev/null || true; wait_gone 30; } \
    || { /usr/bin/pkill -9 -x DoubleClickFixer 2>/dev/null || true; wait_gone 20; } \
    || { printf 'Mouse Double-Click Fixer is still running; quit it and run this again.\n' >&2; exit 1; }
fi

rm -f "$HOME/Library/LaunchAgents/com.doubleclickfixer.app.plist"
rm -rf "$HOME/Library/Application Support/DoubleClickFixer"
rm -f "$HOME/.doubleclick-fixer.json"
rm -f "$HOME/Library/Preferences/com.doubleclickfixer.app.plist"
# The app under its name since 1.0 and the one before (a copy that 0.5.3 or
# earlier updated keeps the old one), in either Applications folder.
for folder in /Applications "$HOME/Applications"; do
  rm -rf "$folder/Mouse Double-Click Fixer.app"
  rm -rf "$folder/DoubleClick Fixer.app"
done
rm -rf "/Applications/DoubleClickFixer.app"  # name used by 0.2 previews
# Forget the Accessibility permission, so a reinstall starts clean.
/usr/bin/tccutil reset Accessibility com.doubleclickfixer.app >/dev/null 2>&1 || true
printf 'Mouse Double-Click Fixer app, startup item, and settings removed.\n'
