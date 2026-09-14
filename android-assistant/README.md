# Android Assistant

An Android controller for the Personal Assistant backend, centered on an
expressive, original 3D character with speech, lip-sync, and visible task state.

## Product direction

The application will use a native Kotlin/Jetpack Compose shell for Android
integration and a Unity module for the avatar. The two layers communicate using
a small typed avatar-state contract. The assistant backend remains responsible
for task orchestration; the Android application presents and controls it.

## Current status

- Original character direction approved.
- Character concept sheet preserved in `design/character-concept-v1.png`.
- Facial, viseme, skeleton, animation, and mobile-performance contract defined.
- Application architecture and prototype acceptance criteria documented.
- Local tooling checked: Android Studio, Java 17, ADB, and Android SDK 36/36.1
  are installed.
- Unity 6.3 LTS (`6000.3.24f1`) and Android Build Support are installed.
- A standalone URP prototype now lives in `unity/AvatarPrototype`. The default
  character is the realistic "Cool Man" by ardhanaputra (CC BY 4.0, modified): the
  generated face has 28 blendshapes, and he greets with a salute and shakes hands on
  success. The cartoon Milo (soft clay, Shin-chan-style, own identity) remains available
  through the in-app LOOK toggle.
- English lip-sync follows pronunciation, not spelling. A bundled CMUdict
  lexicon gives the sounds, speech is synthesized before playback so each word's
  audio frame is known, and the mouth follows the AudioTrack presentation clock
  with coarticulated visemes, sealed p/b/m and f/v closures, and a
  loudness-driven jaw. Validated at 60 fps on a Pixel 10 Pro XL running Android 17.
- A provider-neutral `AssistantResponse` JSON contract and Android-to-Unity
  bridge route reply text into TTS, with duplicate-response protection and a
  credential-free device demo.
- Android SDK 37 is still deferred until the native AppFunctions phase.

## Build sequence

1. ~~Run the Unity prototype on a real Android phone.~~
2. ~~Produce and integrate a detailed rigged character.~~
3. ~~Validate expressions and English TTS lip-sync on-device.~~
4. ~~Define and test the provider-neutral assistant-response ingress.~~
5. Scaffold the Kotlin/Compose controller and embed Unity as a library.
6. Connect task creation and event polling to the Personal Assistant backend.
7. Add microphone and speech recognition.
8. Implement, document, and test Android AppFunctions.

The visual spike is deliberately first. Backend and voice integration should
not hide a character or rig that fails the quality bar.

## Documents

- `docs/architecture.md`: application boundaries and runtime data flow.
- `docs/avatar-rig-contract.md`: required 3D model and animation controls.
- `docs/prototype-acceptance.md`: measurable visual-spike completion criteria.
- `docs/assistant-response-contract.md`: backend/native reply envelope and
  Android-to-Unity delivery boundary.
- `docs/english-lip-sync.md`: speech timing, pronunciation, and viseme mixing.
- `docs/realistic-character-pipeline.md`: converting and validating Cool Man.
- `docs/credits.md`: third-party asset attribution (required for CC BY).
- `docs/rigged-character-pipeline.md`: regenerating and validating the character.

## Credits

"Cool Man" (https://sketchfab.com/3d-models/cool-man-ad14b71697dd4ea7836c1f06c75e5f72)
by ardhanaputra (https://sketchfab.com/ardhanaputra) is licensed under Creative
Commons Attribution 4.0 (http://creativecommons.org/licenses/by/4.0/) and was
modified for this project. See `docs/credits.md`.
