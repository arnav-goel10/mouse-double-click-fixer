#!/bin/bash
# Check that a built "DoubleClick Fixer.app" loads no code named in its
# environment. macOS grants the Accessibility permission to whatever runs as
# the app, so code that gets itself loaded there inherits the grant.
#
# It builds a canary library that leaves a file behind if it is ever loaded,
# then runs the app's --self-test (never the app itself) twice:
#   dyld     DYLD_INSERT_LIBRARIES=<canary>; the hardened runtime must ignore it
#   openssl  OPENSSL_CONF=<a config whose provider module is the canary>; the
#            runtime hook must remove it before hashlib starts OpenSSL
# Each canary file must stay absent, and the self-test must still pass.
#
# Usage: tools/macos_injection_check.sh [path/to/DoubleClick Fixer.app] [--expect-injection]
# --expect-injection turns the check around, for a control build without the
# hardening: both canaries must fire, which shows the check can fail at all.
# Needs clang (Xcode Command Line Tools).
set -euo pipefail

app="dist/DoubleClick Fixer.app"
expect_injection=0
for argument in "$@"; do
  case "$argument" in
    --expect-injection) expect_injection=1 ;;
    *) app="$argument" ;;
  esac
done
binary="$app/Contents/MacOS/DoubleClickFixer"
[[ -x "$binary" ]] || { echo "error: no app at $app" >&2; exit 2; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
marks="${DCF_CANARY_DIR:-$work}"

build_canary() {  # name -> path of a library that writes $marks/dcf-canary-<name> when loaded
  local name="$1"
  cat > "$work/canary.c" <<'C'
#include <fcntl.h>
#include <stdio.h>
#include <unistd.h>

__attribute__((constructor)) static void canary(void) {
    char text[64];
    int fd = open(CANARY_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd >= 0) {
        int length = snprintf(text, sizeof text, "loaded into pid %d\n", getpid());
        write(fd, text, (size_t)length);
        close(fd);
    }
}

/* Lets OpenSSL get as far as initialising it as a provider, and fail. */
int OSSL_provider_init(const void *core, const void *in, const void **out, void **ctx) {
    (void)core; (void)in; (void)out; (void)ctx;
    return 0;
}
C
  clang -dynamiclib -arch "$(uname -m)" -o "$work/dcf-canary-$name.dylib" \
    -DCANARY_PATH="\"$marks/dcf-canary-$name\"" "$work/canary.c"
  # Ad-hoc signed, as an attacker's library would be; arm64 loads nothing unsigned.
  codesign --force --sign - "$work/dcf-canary-$name.dylib" >/dev/null 2>&1
  rm -f "$marks/dcf-canary-$name"
  printf '%s' "$work/dcf-canary-$name.dylib"
}

dyld_canary="$(build_canary dyld)"
openssl_canary="$(build_canary openssl)"
cat > "$work/openssl.cnf" <<CNF
openssl_conf = openssl_init

[openssl_init]
providers = provider_section

[provider_section]
canary = canary_section
default = default_section

[canary_section]
module = $openssl_canary
activate = 1

[default_section]
activate = 1
CNF

failed=0
run() {  # name, then the environment to run the self-test with
  local name="$1"
  shift
  local status=0
  printf '\n== %s: %s\n' "$name" "$*"
  env "$@" "$binary" --self-test || status=$?
  if [[ -e "$marks/dcf-canary-$name" ]]; then
    printf -- '-> canary %s FIRED: %s\n' "$name" "$(cat "$marks/dcf-canary-$name")"
    [[ $expect_injection == 1 ]] || failed=1
  else
    printf -- '-> canary %s did not fire\n' "$name"
    [[ $expect_injection == 0 ]] || failed=1
  fi
  if [[ $expect_injection == 0 && $status != 0 ]]; then
    printf -- '-> the self-test failed (exit %s)\n' "$status"
    failed=1
  fi
}

codesign -dv "$app" 2>&1 | grep -E '^CodeDirectory' || true
run dyld DYLD_INSERT_LIBRARIES="$dyld_canary"
run openssl OPENSSL_CONF="$work/openssl.cnf"

if [[ $failed != 0 ]]; then
  if [[ $expect_injection == 1 ]]; then
    echo "FAILED: a canary did not fire in a build expected to load it; the check proves nothing" >&2
  else
    echo "FAILED: the app loaded code named in its environment, or its self-test failed" >&2
  fi
  exit 1
fi
if [[ $expect_injection == 1 ]]; then
  echo "OK: both canaries fired in this unhardened build, as expected"
else
  echo "OK: neither canary fired and the self-test passed"
fi
