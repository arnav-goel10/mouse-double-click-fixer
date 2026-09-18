#!/usr/bin/env bash
set -euo pipefail

rm -f "$HOME/Library/LaunchAgents/com.doubleclickfixer.app.plist"
rm -rf "$HOME/Library/Application Support/DoubleClickFixer"
rm -f "$HOME/.doubleclick-fixer.json"
rm -f "$HOME/Library/Preferences/com.doubleclickfixer.app.plist"
rm -rf "/Applications/DoubleClick Fixer.app"
rm -rf "/Applications/DoubleClickFixer.app"  # name used by 0.2 previews
printf 'DoubleClick Fixer app, startup item, and settings removed.\n'