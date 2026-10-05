#!/bin/zsh
# Builds and unit-tests the Android app (the console in a native shell).
# Usage: build_shell_android.sh [-PconsoleUrl=https://…]
set -euo pipefail

SCRIPT_DIR=${0:A:h}
SHELL_ROOT="${SCRIPT_DIR:h}/shell"
GRADLE_CACHE_ROOT="${GRADLE_USER_HOME:-${HOME}/.gradle}/wrapper/dists"

if [[ ! -f "$SHELL_ROOT/local.properties" ]]; then
  SDK="${ANDROID_HOME:-${HOME}/Library/Android/sdk}"
  if [[ ! -d "$SDK/platforms" ]]; then
    echo "Android SDK not found. Set ANDROID_HOME or add sdk.dir to $SHELL_ROOT/local.properties." >&2
    exit 1
  fi
  printf 'sdk.dir=%s\n' "$SDK" > "$SHELL_ROOT/local.properties"
fi

GRADLE_BIN="${GRADLE_BIN:-}"
if [[ -z "$GRADLE_BIN" ]]; then
  GRADLE_BIN=$(find "$GRADLE_CACHE_ROOT" -type f -path '*/bin/gradle' | sort -V | tail -1)
fi
if [[ -z "$GRADLE_BIN" || ! -x "$GRADLE_BIN" ]]; then
  echo "Gradle was not found. Set GRADLE_BIN to a Gradle 9.1+ executable." >&2
  exit 1
fi

cd "$SHELL_ROOT"
"$GRADLE_BIN" "$@" :app:testDebugUnitTest :app:assembleDebug
echo "APK: $SHELL_ROOT/app/build/outputs/apk/debug/app-debug.apk"
