# Unity Prototype Implementation

## Baseline

- Unity Editor: `6000.3.24f1`
- Render pipeline: Universal Render Pipeline `17.3.0`
- Android ABI: ARM64
- Scripting backend: IL2CPP
- Minimum Android API: 26
- Requested frame rate: 60 FPS
- Application ID: `com.personalassistant.avatar.prototype`

## Implemented visual behavior

The current scene uses generated Unity primitives as a temporary art stand-in.
It exercises the interface the final rig must support:

- Modes: idle, listening, thinking, speaking, success, and error.
- Emotions: neutral, warm, curious, excited, and concerned.
- Procedural breathing, blinking, gaze saccades, head movement, and gestures.
- Smooth state transitions without resetting the whole avatar.
- Android on-device English TTS with range-anchored viseme animation when the
  Speaking control is selected; the deterministic timeline remains available
  for the automatic visual demo.
- A touch-friendly mode selector, captions, and automatic demo loop.
- A colored state indicator for quick mode recognition.

## Procedural polish pass

The second device-tested stand-in improves presentation without pretending to
replace the final authored model:

- Rebuilt the silhouette with shorter articulated arms, attached hands and
  cuffs, socks, layered shoes, and more balanced chibi proportions.
- Split the jacket into panels with a shirt opening, collar, zipper, and a
  flower-shaped animated status pin.
- Added layered irises, dark pupils, eye highlights, inner ears, cheeks, and
  separate mouth corners that combine with viseme motion.
- Reworked the swept hair silhouette and removed harsh primitive self-shadow
  artifacts from the face.
- Added a soft backdrop halo and a custom safe-area-aware HUD with rounded
  panels, selected-mode feedback, captions, and a live viseme indicator.
- Confirmed direct touch selection of Speaking mode on the Pixel and verified
  that the final focused runtime error log remained empty.

The generated geometry is not evidence that the final character-art quality gate
has passed. The approved rigged character remains the required replacement.

## Character direction v2

The device prototype now follows `design/character-reference-v2.jpeg` instead
of the earlier fashion-oriented character direction:

- A larger rounded head, compact sweatshirt-and-shorts body, shorter limbs,
  smoother hair cap, smaller eyes, and heavier expression-driving brows.
- A restrained red, indigo, and warm-yellow outfit palette, with the animated
  flower pin retained as the assistant-specific state signal.
- Stronger thinking, speaking, and success gestures, plus an open success mouth
  pose that makes state changes readable at phone distance.
- Rebuilt and installed successfully on the Pixel 10 Pro XL after the redesign.

## Rigged character replacement

The procedural character is now a fallback. The primary runtime character is
the Blender-authored `MiloRig.fbx`, with an editable `.blend` source, humanoid
bone hierarchy, rigid skin weights, articulated limb segments, modeled fingers,
layered clothing and shoes, separate facial parts, and 17 mouth blendshapes.
See `docs/rigged-character-pipeline.md` for regeneration and optimization notes.

## Native command boundary

The future Kotlin host sends one high-level JSON command to Unity:

```text
UnitySendMessage(
    "AvatarRuntime",
    "ApplyCommandJson",
    "{\"mode\":\"Speaking\",\"emotion\":\"Warm\",\"intensity\":0.8}"
)
```

Unity owns all animation curves, gaze, blink timing, gesture choice, and viseme
application. Native Android never manipulates bones or facial controls directly.

## English TTS lip sync

The manual Speaking control now calls `SpeakEnglish` with a real line of text.
The runtime builds a lightweight English viseme plan, asks Android's on-device
`TextToSpeech` service to speak it, and uses `onStart`, `onRangeStart`, and
`onDone` callbacks to keep the mouth timeline aligned with the audible phrase.
The Android query declaration is isolated in a small `.androidlib` so Unity can
continue generating the main GameActivity manifest.

See `docs/english-lip-sync.md` for the data flow, accuracy boundary, and the
upgrade path to exact phoneme timestamps.

## Verification

The editor validation checks the build scene, main camera, URP asset, runtime
components, JSON command parsing, and representative English viseme output. Run
it with:

```shell
/Applications/Unity/Hub/Editor/6000.3.24f1/Unity.app/Contents/MacOS/Unity \
  -batchmode -nographics -quit \
  -projectPath /Users/yangyu/personal_assistant/android-assistant/unity/AvatarPrototype \
  -executeMethod PersonalAssistant.Avatar.Editor.PrototypeProjectSetup.ValidatePrototype
```

The Android development APK is generated at:

```text
unity/AvatarPrototype/Builds/Android/avatar-prototype.apk
```

Install it on a connected ARM64 Android phone with:

```shell
adb install -r unity/AvatarPrototype/Builds/Android/avatar-prototype.apk
```

## Pixel validation

Validated on a Google Pixel 10 Pro XL running Android 17 (API 37):

- Cold launch completed and the Unity activity reached the foreground.
- URP materials, camera, lighting, captions, and touch controls rendered at
  the native `1080 × 2404` surface size.
- The automatic loop visibly transitioned across assistant modes.
- Eye reopening was verified after correcting the procedural blink state.
- SurfaceFlinger reported a `16,666,666 ns` refresh/presentation interval,
  corresponding to the requested 60 Hz cadence.
- The final focused log contained no Unity or Android runtime errors.
- Android TTS completed three device utterances; each produced a start event,
  nine range events with audio frame positions, and a done event.
- The provider-neutral mock response completed the full Android bridge path:
  Java delivery, Unity envelope validation, TTS request, nine range callbacks,
  and clean speech completion.
- A synchronized capture was saved locally as
  `captures/pixel-10-pro-xl-english-tts-sync.png`.

The device pass also produced two Android-specific hardening changes: the URP
Lit shader is kept in Always Included Shaders, and `link.xml` preserves collider
classes resolved dynamically by `GameObject.CreatePrimitive` under IL2CPP.
