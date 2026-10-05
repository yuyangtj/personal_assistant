# Realistic Character Pipeline ("Cool Man")

The default assistant character is "Cool Man" by ardhanaputra (CC BY 4.0, see
`docs/credits.md`). The source GLB ships without facial blendshapes, so the conversion
script generates them. The cartoon Milo character remains available as a fallback and
through the **LOOK** toggle in the app.

## Regenerate

From the Android assistant repository root:

```shell
blender --background --python tools/blender/build_cool_man_character.py
```

Pass `-- --no-render` to skip the review renders. Then configure the Unity import
(materials, legacy clips, blendshape normals):

```shell
Unity -batchmode -quit -projectPath unity/AvatarPrototype \
  -executeMethod PersonalAssistant.Avatar.Editor.PrototypeProjectSetup.ConfigureCoolManAsset
```

`CreatePrototypeScene` runs this configuration automatically.

## Outputs

| Path | Contents |
| --- | --- |
| `Resources/Character/CoolManRig.fbx` | Rig, `Assistant_Face` (28 shapes), `Assistant_Body`, `Status_Orb`, salute and shakehand clips |
| `Characters/CoolMan/Textures/` | Textures exported from the GLB |
| `Characters/CoolMan/CoolManMaterials.json` | Material manifest read by `ConfigureCoolManAsset` |
| `Characters/CoolMan/Materials/` | Generated URP Lit materials, remapped onto the FBX |
| `design/source/CoolManRig.blend` | Editable result |
| `design/renders/cool-man/` | Chest-up and three-quarter views, each viseme, expressions |

## Conversion steps

1. **Rig**: delete the stray icosphere, Sketchfab empties, and 117 control/IK bones that
   deform nothing. Rename the 52 Mixamo bones (`Hips`, `Spine`, `Spine1`, `Chest`,
   `Neck`, `Head`, `Shoulder/UpperArm/LowerArm/Hand.L|R`, fingers, legs). Mixamo
   *Left* is `.L`.
2. **Clips**: keep `salute` and `shakehand`, and lock the hips' horizontal location so
   the handshake does not step into the camera.
3. **Meshes**: `Assistant_Face` = skin + teeth + eye discs (+ mouth interior material),
   `Assistant_Body` = body, top, bottom, footwear, hair, glasses. That is two skinned
   renderers and 17,169 triangles.
4. **Bones**: `Jaw` (pivot near the ears, non-deforming) and `Eye.L/R` at the eye-disc
   centres. The discs are weighted to their eye bone, so gaze slides them within the lids.
5. **Mouth interior**: a dark card behind the teeth, plus the skin mouth-bag faces
   behind the lips moved to a matte dark material. Downward-facing faces under the jaw
   are excluded.
6. **Status light**: a small emissive pin ray-cast onto the coat's left lapel, a rigid
   child of `Chest`, pivoted at its own centre.

## Face shapes

Landmarks are measured from the mesh: the lip seam (the open boundary where the lips
meet), mouth corners, eyelid rim loops, and teeth pieces. Two details make the fields
robust on this mesh:

- **Jaw membership** is the ratio of surface distance to a chin seed and an upper-lip
  seed. Paths cannot cross the open lip seam, so the lips separate cleanly while the
  corners and mouth interior blend smoothly.
- **UV-seam duplicates** (vertices at identical positions) are averaged after every
  shape so the surface never tears, except across the lip seam itself.

| Field | Motion |
| --- | --- |
| `jaw(θ)` | Rotates lower-jaw skin, lower teeth, and the lower half of the mouth card around the jaw pivot; fades at the cheeks and throat |
| `apart` | Upper lip up, lower lip down, within a narrow band around the seam |
| `pucker` | Lips move toward the centre and forward |
| `wide` | Corners move out and back |
| `press` | Lips roll inward and flatten |
| `tuck` | Lower lip rises and moves back (f/v) |
| `corner(±)` | Mouth corner and cheek up (smile) or down (frown), per side |
| `blink` | Upper lid rim and lid skin close to the lower rim, passing in front of the eye disc |
| `brow` | Painted brow band: inner raise, outer raise, or lower and pinch |
| `cheek_puff` | Cheek pushes outward |

| Shape | Recipe |
| --- | --- |
| `viseme_PP` | press |
| `viseme_FF` | jaw 1.5°, tuck, slight apart |
| `viseme_TH` | jaw 2.5°, apart |
| `viseme_DD` / `kk` / `nn` | jaw 3° / 3.8° / 2.3°, apart |
| `viseme_CH` | jaw 2°, pucker 0.6, apart |
| `viseme_SS` | jaw 1°, wide 0.55, apart |
| `viseme_RR` | jaw 2°, pucker 0.5, apart |
| `viseme_aa` | jaw 6.5°, apart |
| `viseme_E` / `ih` | jaw 3.5° / 2.5°, wide 0.5 / 0.7, apart |
| `viseme_oh` / `ou` | jaw 4.5° / 2°, pucker 0.7 / 1.0, apart |
| `jawOpen` | jaw 9° (runtime scales loudness-driven jaw by 0.25 for this character) |

The script refuses to export if any check fails. The current results:

- `PP` changes the lip gap by at most 0.3 mm.
- `aa`, `oh` and `ou` open 11.7, 8.6 and 4.7 mm.
- Both blinks fully close a 10.4 mm eye opening.
- No vertex moves more than 25 mm (currently 20 mm).

## Runtime

`AvatarCharacterProfile.CoolMan` holds the rig names and these settings:

- chest-up camera: position (0, 1.57, −2.05), target (0, 1.50, 0), FOV 30;
- character-space gestures;
- gaze scale 0.025;
- the idle pose from the handshake clip's first frame;
- `salute` as the greeting and `shakehand` on Success;
- the attribution text;
- the `male` voice style (see `docs/english-lip-sync.md`).

Speech timing, visemes, and expressions are shared with Milo because the blendshape names
are identical.

## Validation

- `ValidateRiggedAsset` checks both characters:
  - bones, face shapes, skinned-renderer and triangle budgets;
  - for Cool Man, also both clips, URP materials, and a 1.6–2.0 m height.
- Pixel 10 Pro XL:
  - the greeting and handshake play;
  - a screen recording shows lips sealing on m/b/p, rounding, and resting in pauses;
  - blinking works;
  - LOOK switches characters both ways;
  - 59.7 fps with no frame over 34 ms.
