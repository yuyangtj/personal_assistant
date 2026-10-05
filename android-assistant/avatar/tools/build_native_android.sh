#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
REPO_ROOT=${SCRIPT_DIR:h}
UNITY_EXPORT="$REPO_ROOT/unity/AvatarPrototype/Builds/AndroidExport"
NATIVE_ROOT="$REPO_ROOT/native"
UNITY_EDITOR="/Applications/Unity/Hub/Editor/6000.3.24f1/Unity.app/Contents/MacOS/Unity"
GRADLE_CACHE_ROOT="${GRADLE_USER_HOME:-${HOME}/.gradle}/wrapper/dists"

# Re-export when asked (--reexport), when no export exists, or when Unity sources changed since.
REEXPORT=false
[[ "${1:-}" == "--reexport" ]] && REEXPORT=true
if [[ ! -d "$UNITY_EXPORT/unityLibrary" ]]; then
  REEXPORT=true
elif [[ -n "$(find "$REPO_ROOT/unity/AvatarPrototype/Assets" -newer "$UNITY_EXPORT/unityLibrary/build.gradle" -type f ! -name '*.meta' -print -quit)" ]]; then
  REEXPORT=true
fi

if [[ "$REEXPORT" == true ]]; then
  echo "Exporting the Unity Android library…"
  "$UNITY_EDITOR" -batchmode -nographics -quit \
    -projectPath "$REPO_ROOT/unity/AvatarPrototype" \
    -executeMethod PersonalAssistant.Avatar.Editor.PrototypeProjectSetup.ExportAndroidLibrary \
    -logFile "$REPO_ROOT/unity/AvatarPrototype/Logs/native-export.log"
fi

cp "$UNITY_EXPORT/local.properties" "$NATIVE_ROOT/local.properties"

GRADLE_BIN="${GRADLE_BIN:-}"
if [[ -z "$GRADLE_BIN" ]]; then
  GRADLE_BIN=$(find "$GRADLE_CACHE_ROOT" -type f -path '*/bin/gradle' | sort -V | tail -1)
fi
if [[ -z "$GRADLE_BIN" || ! -x "$GRADLE_BIN" ]]; then
  echo "Gradle was not found. Set GRADLE_BIN to a Gradle 9.1+ executable." >&2
  exit 1
fi

cd "$NATIVE_ROOT"
"$GRADLE_BIN" :app:assembleDebug
