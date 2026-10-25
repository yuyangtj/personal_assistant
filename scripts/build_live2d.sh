#!/usr/bin/env bash
# Build the console's Live2D avatar from the Live2D Cubism SDK for Web and a model.
#
#   scripts/build_live2d.sh <sdk-dir> <model-runtime-dir> <out-dir>
#
# Example:
#   scripts/build_live2d.sh ~/Downloads/CubismSdkForWeb-5-r.5 ~/Downloads/ren_en/runtime ./avatar-assets
#
# The SDK and the model are Live2D's, under their licences, and are never committed: this
# copies the SDK's sample app into a temporary folder, makes a few small changes (one
# model, no background, a transparent canvas, a hook for the console), adds our entry
# (avatar/live2d/entry.ts) and bundles it. <out-dir> then holds everything the server
# serves at /ui/avatar-assets/ (see ASSISTANT_AVATAR_ASSETS_DIR in docs/remote-hosting.md).
set -euo pipefail
SDK="$(cd "$1" && pwd)"; MODEL="$(cd "$2" && pwd)"; OUT="$3"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
MODEL_JSON="$(ls "$MODEL"/*.model3.json | head -1)"
NAME="$(basename "$MODEL_JSON" .model3.json)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cp -R "$SDK/Framework" "$WORK/Framework"
mkdir -p "$WORK/app"
cp -R "$SDK/Samples/TypeScript/Demo/src" "$WORK/app/src"
cp "$SDK/Samples/TypeScript/Demo/package.json" "$SDK/Samples/TypeScript/Demo/package-lock.json" \
   "$SDK/Samples/TypeScript/Demo/tsconfig.json" "$WORK/app/"
cp "$REPO/avatar/live2d/entry.ts" "$WORK/app/src/entry.ts"

python3 - "$WORK/app/src" "$NAME" <<'PY'
import re, sys
src, name = sys.argv[1], sys.argv[2]
def edit(path, pairs):
    path = f"{src}/{path}"
    s = open(path).read()
    for old, new in pairs:
        if old not in s:
            sys.exit(f"build_live2d: {path} changed in this SDK version: {old[:60]!r}")
        s = s.replace(old, new, 1)
    open(path, "w").write(s)
s = open(f"{src}/lappdefine.ts").read()
s = re.sub(r"export const ModelDir: string\[\] = \[[^\]]*\];", f"export const ModelDir: string[] = ['{name}'];", s)
s = s.replace("export const ResourcesPath = '../../Resources/';", "export const ResourcesPath = '/ui/avatar-assets/Resources/';")
s = s.replace("export const ShaderPath = '../../Framework/Shaders/WebGL/';", "export const ShaderPath = '/ui/avatar-assets/Framework/Shaders/WebGL/';")
open(f"{src}/lappdefine.ts", "w").write(s)
edit("lappview.ts", [("    let imageName = '';", "    let imageName = '';\n    if (imageName === '') return; // no background or buttons in the avatar")])
edit("lappsubdelegate.ts", [("gl.clearColor(0.0, 0.0, 0.0, 1.0);", "gl.clearColor(0.0, 0.0, 0.0, 0.0);")])
edit("lappdelegate.ts", [
    ("      canvas.style.width = `${width}vw`;\n      canvas.style.height = `${height}vh`;",
     "      canvas.style.width = '100%';\n      canvas.style.height = '100%';"),
    ("      document.body.appendChild(canvas);", "      ((window as any).__live2dHost || document.body).appendChild(canvas);"),
])
edit("lappmodel.ts", [("""    this._updateScheduler.onLateUpdate(this._model, deltaTimeSeconds);

    this._model.update();""", """    this._updateScheduler.onLateUpdate(this._model, deltaTimeSeconds);

    // The console drives the mouth (lip sync), gaze and head on top of the model's motion.
    const control = (window as any).__live2dControl;
    (window as any).__live2dModel = this;
    if (control) {
      const ids = CubismFramework.getIdManager();
      for (const [id, value] of Object.entries(control.params || {})) {
        // "+Param…" adds to the model's own motion (head sway); "Param…" replaces it.
        if (id.startsWith('+')) this._model.addParameterValueById(ids.getId(id.slice(1)), value as number);
        else this._model.setParameterValueById(ids.getId(id), value as number);
      }
    }

    this._model.update();""")])
edit("lapplive2dmanager.ts", [("""      // 必要があればここで乗算
      if (this._viewMatrix != null) {""", """      // Framing for the avatar: zoom in on the head and shoulders.
      const frame = ((window as any).__live2dControl || {}).frame || { zoom: 1, y: 0 };
      projection.scaleRelative(frame.zoom, frame.zoom);
      projection.translateRelative(0, -frame.y);

      // 必要があればここで乗算
      if (this._viewMatrix != null) {""")])
PY

cat > "$WORK/app/vite.config.mts" <<'VITE'
import { defineConfig } from 'vite';
import path from 'path';
export default defineConfig({
  resolve: { extensions: ['.ts', '.js'], alias: { '@framework': path.resolve(__dirname, '../Framework/src') } },
  build: {
    outDir: 'dist', emptyOutDir: true, sourcemap: false, target: 'es2020',
    lib: { entry: 'src/entry.ts', formats: ['iife'], name: 'Live2DAvatar', fileName: () => 'live2d-avatar.js' },
  },
});
VITE
(cd "$WORK/app" && npm ci --no-audit --no-fund --ignore-scripts >/dev/null && npx vite build >/dev/null)

mkdir -p "$OUT"
rm -rf "$OUT/Resources" "$OUT/Framework" "$OUT/Core"
mkdir -p "$OUT/Core" "$OUT/Framework/Shaders" "$OUT/Resources"
cp "$WORK/app/dist/live2d-avatar.js" "$OUT/"
cp "$SDK/Core/live2dcubismcore.min.js" "$OUT/Core/"
cp -R "$SDK/Framework/Shaders/WebGL" "$OUT/Framework/Shaders/"
cp -R "$MODEL" "$OUT/Resources/$NAME"
# The sample loads <name>/<name>.model3.json, which is how the model ships.
cat > "$OUT/manifest.json" <<JSON
{"model": "$NAME", "script": "live2d-avatar.js", "core": "Core/live2dcubismcore.min.js",
 "frame": {"zoom": 4.2, "y": 0.7}}
JSON
du -sh "$OUT" | awk '{print "built " $1 " into " $2}'
