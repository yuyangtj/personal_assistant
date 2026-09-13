# Avatar Rig Contract

This contract uses `design/character-reference-v2.jpeg` as the current visual
direction for an animation-ready, mobile-friendly 3D character. Its compact
proportions, clear brows, and broad pose language are the primary references;
the production character should retain its own identity and the assistant's
flower-shaped state indicator.

## Deliverables

- Source scene with editable rig and blendshapes.
- Unity-ready FBX with humanoid skeleton.
- Optional GLB or VRM interchange export.
- Albedo, normal, and optional packed mask textures.
- Test scene demonstrating every facial control and animation clip.
- Table mapping exported blendshape and animation names to this contract.

## Mobile asset budget

- 25,000–50,000 rendered triangles for the character.
- No more than three skinned mesh renderers.
- Prefer two materials: body/clothing and eyes/face.
- 2K texture maximum per primary map; use 1K where visually equivalent.
- Avoid transparent hair cards when solid stylized geometry works.
- Keep deformation stable under medium and low precision mobile shaders.

These are prototype ceilings rather than quality targets. Measure the complete
scene on the target phone before tightening them.

## Skeleton

Required body chain:

```text
Root
└── Hips
    ├── Spine → Chest → UpperChest → Neck → Head
    │                                  ├── Eye.L
    │                                  ├── Eye.R
    │                                  └── Jaw
    ├── Shoulder.L → UpperArm.L → LowerArm.L → Hand.L
    ├── Shoulder.R → UpperArm.R → LowerArm.R → Hand.R
    ├── UpperLeg.L → LowerLeg.L → Foot.L → Toe.L
    └── UpperLeg.R → LowerLeg.R → Foot.R → Toe.R
```

Finger bones are optional for the first prototype. Eye bones must rotate without
distorting the eyelids. Jaw motion must blend cleanly with every speaking shape.

## Lip-sync shapes

All speech shapes must be independently adjustable from 0.0 to 1.0 and combine
cleanly with expressions:

| Shape | Main sounds |
| --- | --- |
| `viseme_sil` | neutral/rest |
| `viseme_PP` | p, b, m |
| `viseme_FF` | f, v |
| `viseme_TH` | th |
| `viseme_DD` | t, d |
| `viseme_kk` | k, g |
| `viseme_CH` | ch, j, sh |
| `viseme_SS` | s, z |
| `viseme_nn` | n, l |
| `viseme_RR` | r |
| `viseme_aa` | broad open vowel |
| `viseme_E` | e/eh |
| `viseme_ih` | i/ih |
| `viseme_oh` | rounded o |
| `viseme_ou` | tight rounded oo |

Required supporting controls:

- `jawOpen`
- `mouthSmile.L`, `mouthSmile.R`
- `mouthFrown.L`, `mouthFrown.R`
- `mouthWide`, `mouthNarrow`
- `cheekPuff`, `cheekSquint.L`, `cheekSquint.R`

## Eyes and brows

- `eyeBlink.L`, `eyeBlink.R`
- `eyeWide.L`, `eyeWide.R`
- `eyeSquint.L`, `eyeSquint.R`
- `browInnerUp`
- `browOuterUp.L`, `browOuterUp.R`
- `browDown.L`, `browDown.R`

Blinking must fully close without intersections. Left and right controls remain
separate even when most animation drives them together.

## Expression poses

Create additive poses for:

- `expression_neutral`
- `expression_warm`
- `expression_curious`
- `expression_excited`
- `expression_concerned`

Expressions must combine with lip-sync. They may include brows, eyelids, cheeks,
and restrained mouth-corner motion, but must not force the jaw closed.

## Animation clips

| Clip | Loop | Purpose |
| --- | --- | --- |
| `idle_breathe` | Yes | Primary quiet idle |
| `idle_shift` | Yes | Occasional weight shift |
| `listen_attentive` | Yes | Eye contact and slight forward lean |
| `think_soft` | Yes | Subtle glance and head movement |
| `speak_neutral` | Yes | Low-amplitude speech gestures |
| `speak_energetic` | Yes | More expressive speech gestures |
| `react_success` | No | Short positive acknowledgement |
| `react_error` | No | Restrained concerned reaction |
| `greet` | No | Initial welcome gesture |

Eye saccades, gaze targets, blinking, and visemes will be procedural rather than
baked into body clips.

## Shader direction

- Soft cel shading with two or three readable light bands.
- Subtle rim light, not a glowing outline.
- Stable skin tone under different display brightness levels.
- Controlled highlights in eyes and hair.
- Avoid heavy post-processing that makes the UI or captions harder to read.

## Export validation

The asset is accepted only when:

- Identity and proportions match every turnaround view.
- No visible clipping occurs in the required poses.
- Every named control is discoverable programmatically.
- Visemes remain readable when combined with all five expressions.
- Head and eye tracking do not produce eyelid or neck artifacts.
- The Unity test scene loads with no missing materials or animation bindings.
