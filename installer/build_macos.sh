#!/usr/bin/env bash
# Build DoubleClickFixer.app and a DMG from macOS.
set -euo pipefail

# Skip the install step when the environment already has what it needs
# (a uv-managed virtualenv has no pip of its own, for example).
if ! python3 -c "import PyInstaller, PySide6" 2>/dev/null; then
  python3 -m pip install -r requirements.txt pyinstaller
fi

python3 -m PyInstaller --clean --noconfirm doubleclick-fixer.spec
test -d dist/DoubleClickFixer.app

# Ad-hoc signing keeps the Accessibility grant stable across launches of the
# same build. A release still needs a Developer ID signature and notarization.
codesign --force --deep --sign - dist/DoubleClickFixer.app

rm -f dist/DoubleClickFixer.dmg
hdiutil create -volname "DoubleClick Fixer" -srcfolder dist/DoubleClickFixer.app \
  -ov -format UDZO dist/DoubleClickFixer.dmg
printf 'Built dist/DoubleClickFixer.app and dist/DoubleClickFixer.dmg\n'
