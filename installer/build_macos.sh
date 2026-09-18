#!/usr/bin/env bash
# Build "DoubleClick Fixer.app" and a DMG from macOS.
set -euo pipefail

APP="dist/DoubleClick Fixer.app"

# Skip the install step when the environment already has what it needs
# (a uv-managed virtualenv has no pip of its own, for example).
if ! python3 -c "import PyInstaller, PySide6, dmgbuild" 2>/dev/null; then
  python3 -m pip install -r requirements.txt pyinstaller dmgbuild
fi

python3 -m PyInstaller --clean --noconfirm doubleclick-fixer.spec
test -d "$APP"

# Ad-hoc signing keeps the Accessibility grant stable across launches of the
# same build. A release still needs a Developer ID signature and notarization.
# Extended attributes (Finder info, provenance) make codesign refuse the bundle.
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"

# The disk image opens to a designed window: the app, an arrow and the
# Applications folder, so installing is one drag. dmgbuild writes Finder's
# layout file directly, so this needs no Finder scripting or permissions.
python3 -m dmgbuild -s installer/dmg_settings.py -D app="$APP" \
  "DoubleClick Fixer" dist/DoubleClickFixer.dmg
printf 'Built %s and dist/DoubleClickFixer.dmg\n' "$APP"
