# Rigged Character Pipeline

The detailed Milo prototype is an authored Blender asset rather than Unity
runtime primitives. Its source and exported runtime asset live at:

- `unity/AvatarPrototype/Assets/AvatarPrototype/Resources/Character/MiloRig.blend`
- `unity/AvatarPrototype/Assets/AvatarPrototype/Resources/Character/MiloRig.fbx`

## Regenerate the asset

Run the versioned generator from the Android assistant repository root:

```shell
blender --background --python tools/blender/build_milo_character.py
```

The generator rebuilds the `.blend` source, exports the `.fbx`, and renders
`design/renders/rigged-character-v1.png` for visual review.

## Included controls

- Humanoid-style root, hips, spine, chest, neck, head, two-piece arms, hands,
  two-piece legs, and feet.
- Rigid skin weights for reliable FBX import and articulated state gestures.
- Separate irises, eyelids, brows, cheeks, teeth, tongue, and status flower.
- Seventeen mouth blendshapes: the fourteen speech visemes used by the rig
  contract, a silent/rest shape, smile, and concerned expression.
- Runtime URP material remapping so the colors remain deterministic on Android.
- Automatic fallback to the procedural character if the FBX is unavailable or
  its required nodes cannot be found.

## Validation

`PrototypeProjectSetup.ValidateRiggedAsset` checks required bones, facial nodes,
and every required speech blendshape. The controller logs
`RIGGED_AVATAR_ACTIVE` with the imported renderer and blendshape counts.

The first detailed version intentionally keeps parts modular for iteration. It
currently imports as 70 small skinned renderers. Before a production release,
merge non-animated parts by material, atlas the palette, and target three or
fewer skinned renderers without merging the mouth blendshape mesh.
