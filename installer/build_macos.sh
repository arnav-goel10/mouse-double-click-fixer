#!/usr/bin/env bash
set -euo pipefail
python3 -m pip install -r requirements.txt pyinstaller
python3 -m PyInstaller --clean --noconfirm --target-architecture universal2 doubleclick-fixer.spec
test -d dist/DoubleClickFixer.app
hdiutil create -volname "DoubleClick Fixer" -srcfolder dist/DoubleClickFixer.app -ov -format UDZO dist/DoubleClickFixer.dmg
printf 'Built dist/DoubleClickFixer.app and dist/DoubleClickFixer.dmg\n'
