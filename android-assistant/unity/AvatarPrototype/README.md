# Avatar Prototype (Unity)

Standalone Unity 6.3 LTS URP visual spike for the Android assistant avatar.

## Open and run

1. Add this folder in Unity Hub or open it with Unity `6000.3.24f1`.
2. Open `Assets/AvatarPrototype/Scenes/AvatarPrototype.unity`.
3. Press Play. The demo cycles through all six assistant modes every four seconds.
4. Use the on-screen mode buttons to stop auto-demo and inspect an individual state.

The first editor import creates the scene through the command below. It has
already been run in this workspace:

```text
Personal Assistant > Create Prototype Scene
```

## Android build

Use `Personal Assistant > Build Android APK`, or run the editor method
`PersonalAssistant.Avatar.Editor.PrototypeProjectSetup.CreateAndBuildAndroid`
in batch mode. The output is `Builds/Android/avatar-prototype.apk`.

For a device build without development instrumentation, use
`Personal Assistant > Build Android Release APK` or execute
`PersonalAssistant.Avatar.Editor.PrototypeProjectSetup.BuildAndroidRelease`.

## Native bridge contract

The future Kotlin host can call Unity's `UnitySendMessage` with:

```text
gameObject: AvatarRuntime
method: ApplyCommandJson
payload: {"mode":"Speaking","emotion":"Warm","intensity":0.8}
```

The runtime loads the realistic `Resources/Character/CoolManRig.fbx` by default
(see `../../docs/realistic-character-pipeline.md`) and the cartoon
`Resources/Character/MiloRig.fbx` through the LOOK toggle
(see `../../docs/rigged-character-pipeline.md`). The primitive avatar in
`ProceduralAvatarController` is only the fallback when that asset is missing or
incomplete.

Validation methods (run with `-executeMethod`): `ConfigureCoolManAsset`, `ValidateSpeech`,
`ValidateRiggedAsset`, `ValidatePrototype`, and `CapturePrototypeFrame`.
