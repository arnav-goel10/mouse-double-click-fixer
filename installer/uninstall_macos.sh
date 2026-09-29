#!/usr/bin/env bash
set -euo pipefail

# Quit a running copy first, so it can't recreate its settings on the way out.
osascript -e 'tell application id "com.doubleclickfixer.app" to quit' 2>/dev/null || true
sleep 1; pkill -x DoubleClickFixer 2>/dev/null || true

rm -f "$HOME/Library/LaunchAgents/com.doubleclickfixer.app.plist"
rm -rf "$HOME/Library/Application Support/DoubleClickFixer"
rm -f "$HOME/.doubleclick-fixer.json"
rm -f "$HOME/Library/Preferences/com.doubleclickfixer.app.plist"
rm -rf "/Applications/DoubleClick Fixer.app"
rm -rf "/Applications/DoubleClickFixer.app"  # name used by 0.2 previews
# Forget the Accessibility permission, so a reinstall starts clean.
tccutil reset Accessibility com.doubleclickfixer.app >/dev/null 2>&1 || true
printf 'DoubleClick Fixer app, startup item, and settings removed.\n'