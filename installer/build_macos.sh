#!/bin/bash
# Build "DoubleClick Fixer.app", the DMG people install from, and the zip the
# in-app updater downloads.
#
# Signing: macOS remembers the Accessibility permission against the app's
# signing certificate. Releases must therefore always be signed with the same
# certificate, or every update would ask for permission again. The identity is
# taken from DCF_SIGN_IDENTITY / DCF_SIGN_KEYCHAIN (CI), or from the local
# signing keychain in ~/.doubleclick-fixer-signing; without either the build
# is ad-hoc signed, which is fine for trying things out but not for releases.
set -euo pipefail

APP="dist/DoubleClick Fixer.app"
LOCAL_SIGNING="$HOME/.doubleclick-fixer-signing"

# Skip the install step when the environment already has what it needs
# (a uv-managed virtualenv has no pip of its own, for example).
if ! python3 -c "import PyInstaller, PySide6, dmgbuild" 2>/dev/null; then
  python3 -m pip install -r requirements.txt pyinstaller dmgbuild
fi

python3 -m PyInstaller --clean --noconfirm doubleclick-fixer.spec
test -d "$APP"

# Sign and package outside the project folder. Synced folders (iCloud Drive's
# Desktop & Documents, for example) keep adding extended attributes to files,
# and codesign refuses a bundle that carries them.
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
signed="$work/DoubleClick Fixer.app"
ditto --norsrc --noextattr "$APP" "$signed"
xattr -cr "$signed"

# macOS 26+ draws layered "Liquid Glass" icons, with dark, tinted and clear
# variants, from an Icon Composer file compiled into Assets.car. Older macOS,
# and builds without a new enough Xcode, use AppIcon.icns as before.
icon_work="$work/icon"
mkdir -p "$icon_work"
if xcrun actool installer/assets/AppIcon.icon --compile "$icon_work" --app-icon AppIcon \
    --platform macosx --minimum-deployment-target 13.0 \
    --output-partial-info-plist "$icon_work/partial.plist" >/dev/null 2>&1 \
    && [[ -f "$icon_work/Assets.car" ]]; then
  cp "$icon_work/Assets.car" "$signed/Contents/Resources/Assets.car"
  /usr/libexec/PlistBuddy -c "Add :CFBundleIconName string AppIcon" "$signed/Contents/Info.plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Set :CFBundleIconName AppIcon" "$signed/Contents/Info.plist"
  printf 'Added the layered macOS 26+ icon\n'
else
  printf 'warning: this Xcode cannot compile AppIcon.icon; using the flat icon only\n' >&2
fi

identity="${DCF_SIGN_IDENTITY:-}"
keychain="${DCF_SIGN_KEYCHAIN:-}"
if [[ -z "$identity" && -f "$LOCAL_SIGNING/signing.keychain-db" ]]; then
  keychain="$LOCAL_SIGNING/signing.keychain-db"
  security unlock-keychain -p "$(cat "$LOCAL_SIGNING/keychain.password")" "$keychain"
  identity="$(security find-identity -p codesigning "$keychain" | awk '/DoubleClick Fixer Signing/ {print $2; exit}')"
fi
if [[ -n "$identity" ]]; then
  # --keychain only when one is named; otherwise the default search list.
  codesign --force --deep --timestamp=none ${keychain:+--keychain "$keychain"} --sign "$identity" "$signed"
  printf 'Signed with %s\n' "$identity"
else
  codesign --force --deep --sign - "$signed"
  printf 'warning: ad-hoc signed; Accessibility permission will not carry over to updates\n' >&2
fi
codesign --verify --deep --strict "$signed"

# The disk image opens to a designed window: the app, an arrow and the
# Applications folder, so installing is one drag. dmgbuild writes Finder's
# layout file directly, so this needs no Finder scripting or permissions.
rm -f dist/DoubleClickFixer.dmg dist/DoubleClickFixer-macos.zip
python3 -m dmgbuild -s installer/dmg_settings.py -D app="$signed" \
  "DoubleClick Fixer" dist/DoubleClickFixer.dmg

# What the in-app updater downloads: the signed bundle, zipped with ditto so
# the signature and symlinks survive.
ditto -c -k --norsrc --noextattr --keepParent "$signed" dist/DoubleClickFixer-macos.zip

# Keep a copy of the signed app in dist for trying it out locally.
rm -rf "$APP"
ditto "$signed" "$APP"
printf 'Built %s, dist/DoubleClickFixer.dmg and dist/DoubleClickFixer-macos.zip\n' "$APP"
