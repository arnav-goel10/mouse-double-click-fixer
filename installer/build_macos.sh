#!/bin/bash
# Build "Mouse Double-Click Fixer.app", the DMG people install from, and the
# zip the in-app updater downloads.
#
# Signing: macOS remembers the Accessibility permission against the app's
# signing certificate. Releases must therefore always be signed with the same
# certificate, or every update would ask for permission again. The identity is
# taken from DCF_SIGN_IDENTITY / DCF_SIGN_KEYCHAIN (CI), or from the local
# signing keychain in ~/.doubleclick-fixer-signing; without either the build
# is ad-hoc signed, which is fine for trying things out but not for releases.
set -euo pipefail

# The name people see, from app/__init__.py (DISPLAY_NAME). The release files
# keep their names (DoubleClickFixer.dmg and so on): installed copies and
# download links look for them.
NAME="$(sed -n 's/^DISPLAY_NAME = "\(.*\)"$/\1/p' app/__init__.py)"
[[ -n "$NAME" ]] || { printf 'error: no DISPLAY_NAME in app/__init__.py\n' >&2; exit 1; }
APP="dist/$NAME.app"
LOCAL_SIGNING="$HOME/.doubleclick-fixer-signing"

# Build with exactly the pinned packages and build tools, each file checked
# against its hash (requirements-build.txt; see requirements-build.in). A
# uv-managed virtualenv has no pip of its own; uv installs the same files.
install_pinned() {
  if python3 -m pip --version >/dev/null 2>&1; then
    python3 -m pip install --require-hashes --only-binary :all: -r requirements-build.txt
  elif command -v uv >/dev/null 2>&1; then
    uv pip install --python "$(command -v python3)" --require-hashes --only-binary :all: -r requirements-build.txt
  else
    printf 'error: neither pip nor uv can install packages for %s\n' "$(command -v python3)" >&2
    return 1
  fi
}
if ! install_pinned; then
  printf 'error: could not install requirements-build.txt. Build in a virtualenv:\n' >&2
  printf '  python3 -m venv .venv-build && . .venv-build/bin/activate && bash installer/build_macos.sh\n' >&2
  exit 1
fi

python3 -m PyInstaller --clean --noconfirm doubleclick-fixer.spec
test -d "$APP"

# Sign and package outside the project folder. Synced folders (iCloud Drive's
# Desktop & Documents, for example) keep adding extended attributes to files,
# and codesign refuses a bundle that carries them.
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
signed="$work/$NAME.app"
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
# The hardened runtime makes dyld ignore DYLD_INSERT_LIBRARIES and the like,
# so no other program can load its code into this one and borrow its
# Accessibility permission. The entitlements file says what it still allows
# and why. It changes nothing in the designated requirement, so the
# permission carries over from earlier versions.
hardening=(--options runtime --entitlements installer/entitlements.plist)
if [[ -n "$identity" ]]; then
  # --keychain only when one is named; otherwise the default search list.
  codesign --force --deep --timestamp=none "${hardening[@]}" ${keychain:+--keychain "$keychain"} --sign "$identity" "$signed"
  printf 'Signed with %s\n' "$identity"
else
  codesign --force --deep "${hardening[@]}" --sign - "$signed"
  printf 'warning: ad-hoc signed; Accessibility permission will not carry over to updates\n' >&2
fi
codesign --verify --deep --strict "$signed"
signature="$(codesign -dv "$signed" 2>&1)"
if ! grep -Eq 'flags=0x[0-9a-f]+\([^)]*runtime' <<<"$signature"; then
  printf '%s\nerror: the app is not signed with the hardened runtime\n' "$signature" >&2
  exit 1
fi

# Launch the signed app's self-test: it catches a build that crashes at
# launch, or that the hardened runtime breaks, before anything is packaged.
# It never starts the app itself (see app/selftest.py). The second run loads
# the real Cocoa platform plugin, which must load under the hardened runtime
# too (it still opens no window).
"$signed/Contents/MacOS/DoubleClickFixer" --self-test
QT_QPA_PLATFORM=cocoa "$signed/Contents/MacOS/DoubleClickFixer" --self-test

# Nothing named in the app's environment may load into it or run as it:
# libraries (DYLD_INSERT_LIBRARIES, OPENSSL_CONF), or programs and shell
# start-up files (PATH, BASH_ENV, exported functions).
bash tools/macos_injection_check.sh "$signed"

# The notices it ships must be exactly what its own files call for.
python3 tools/make_notices.py --bundle "$signed" --check --output "$signed/Contents/Resources/THIRD_PARTY_NOTICES.md"

# The disk image opens to a designed window: the app, an arrow and the
# Applications folder, so installing is one drag. dmgbuild writes Finder's
# layout file directly, so this needs no Finder scripting or permissions. The
# volume, and so the window, is named after the app.
rm -f dist/DoubleClickFixer.dmg dist/DoubleClickFixer-macos.zip
python3 -m dmgbuild -s installer/dmg_settings.py -D app="$signed" \
  "$NAME" dist/DoubleClickFixer.dmg

# What the in-app updater downloads: the signed bundle, zipped with ditto so
# the signature and symlinks survive. Copies of 0.5.3 and earlier install the
# one app they find in it under their own bundle's name; later ones install it
# under this name and remove the old one (app/updater.py).
ditto -c -k --norsrc --noextattr --keepParent "$signed" dist/DoubleClickFixer-macos.zip

# Keep a copy of the signed app in dist for trying it out locally.
rm -rf "$APP"
ditto "$signed" "$APP"
printf 'Built %s, dist/DoubleClickFixer.dmg and dist/DoubleClickFixer-macos.zip\n' "$APP"
