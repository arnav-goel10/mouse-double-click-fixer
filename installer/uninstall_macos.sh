#!/usr/bin/env bash
set -euo pipefail

rm -f "$HOME/Library/LaunchAgents/com.doubleclickfixer.app.plist"
rm -rf "$HOME/Library/Application Support/DoubleClickFixer"
rm -f "$HOME/.doubleclick-fixer.json"
rm -f "$HOME/Library/Preferences/com.doubleclickfixer.app.plist"
rm -rf "/Applications/DoubleClickFixer.app"
printf 'DoubleClick Fixer app, startup item, and settings removed.\n'