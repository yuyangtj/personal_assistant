# Rigged Character Pipeline

Milo v3 is a soft clay character in the style of the reference sheet
(`design/character-reference-v2.jpeg`): rice-ball head with puffy lower cheeks,
thick caterpillar brows, small body in a red long-sleeve shirt and blue shorts.
Milo keeps his own identity: face, forehead lock, and the flower status pin.

The asset is generated entirely by a versioned script:

- `unity/AvatarPrototype/Assets/AvatarPrototype/Resources/Character/MiloRig.blend`
- `unity/AvatarPrototype/Assets/AvatarPrototype/Resources/Character/MiloRig.fbx`

## Regenerate

From the Android assistant repository root:

```shell
blender --background --python tools/blender/build_milo_character.py
python3 tools/lexicon/build_lexicon.py   # only when the speech lexicon changes
```

The Blender script rebuilds the `.blend` and `.fbx` and renders review images to
`design/renders/`: `rigged-character-v3.png`, `rigged-character-v3-three-quarter.png`,
`rigged-character-v3-head.png` (3/4 head close-up),
and `v3-face-*.png` close-ups of the key mouth shapes and a wink.

## Construction

- **Head**: one 72×48 surface shaped by `head_point`: about as tall as it is
  wide, a full rounded forehead dome, big cheek lobes bulging wide and forward in
  the lower half, and a broad soft jaw. Face features are placed relative to
  `HEAD_CENTER`, so the head can be reshaped without moving them by hand.
- **Face** (`Milo_Face`): every feature is a decal projected onto the head
  surface with a BVH ray cast, so it hugs the curvature: eye whites, pupils and
  glints on `Eye.L/R` bones, lid lines, brows, blush, nose, lip rim, mouth
  cavity, teeth, and tongue. Each blendshape is produced by regenerating the
  same topology from different parameters, so shapes are real vertex motion
  rather than scaling.
- **Mouth shapes**: an aperture curve (width, upper and lower opening, corners,
  protrusion) drives the lip rim, cavity, teeth, and tongue together. PP presses
  the lips, FF shows the upper teeth over a tucked lower lip, TH brings the
  tongue forward, and oh/ou round and push the lips.
- **Hair** (`Milo_Hair`): a solidified cap over the crown, sides, and back with
  a flat hairline straight across the forehead, plus Milo's signature lock.
- **Proportions**: a big head on a stocky body. The torso and shorts are wide,
  the arms are short with mitten-ball hands, and the legs are short. The eyes are
  small whites with large black pupils and a full dark outline; the brows are
  thick and bushy; the ears are big and round at eye height; the resting mouth
  is small (`MOUTH_SCALE`).
- **Body** (`Milo_Body`): lathe torso, ribbed hem and collar, sleeves along the
  arm bones, mitten hands with thumbs, shorts, socks, and shoes. Weights blend
  smoothly between neighbouring bones by distance to each bone segment.
- **Status_Orb**: a rigid child of `Chest`, pivoted at its own centre so the
  runtime pulse scales it in place.
- Every part is wound to face outward, because Unity culls back faces.

## Runtime contract

- Three skinned renderers plus the orb; 33,300 triangles.
- Colours are sRGB vertex colours. Vertex alpha is a shading class for
  `Shaders/MiloClayToon.shader` (skin, cloth, glossy hair, dark detail, bright
  detail). The shader is soft two-band clay lighting with ambient SH, broad
  highlights, and a gentle rim.
- `Milo_Face` blendshapes: `viseme_sil`, `viseme_PP` … `viseme_ou`, `jawOpen`,
  `mouthSmile.L/R`, `mouthFrown.L/R`, `eyeBlink.L/R`, `browInnerUp`,
  `browOuterUp.L/R`, `browDown.L/R`, `cheekPuff`.
- The controller falls back to the procedural stand-in if required nodes or
  viseme shapes are missing.

## Validation

`PrototypeProjectSetup.ValidateRiggedAsset` checks the bones, the three meshes,
vertex colours, every blendshape above, the three-skinned-renderer limit, and the
50,000-triangle budget (`RIGGED_ASSET_VALIDATION_PASSED`). At runtime the
controller logs `RIGGED_AVATAR_ACTIVE` with renderer and blendshape counts.
