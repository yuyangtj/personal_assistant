# Credits and third-party assets

## "Cool Man" 3D character

- **Title:** Cool Man
- **Author:** ardhanaputra — https://sketchfab.com/ardhanaputra
- **Source:** https://sketchfab.com/3d-models/cool-man-ad14b71697dd4ea7836c1f06c75e5f72
- **Licence:** Creative Commons Attribution 4.0 (CC BY 4.0) — http://creativecommons.org/licenses/by/4.0/
- **Original file in this repository:** `design/source/cool_man.glb`
- **Modified:** yes. `tools/blender/build_cool_man_character.py` removes the control/IK
  rig bones and renames the skeleton, keeps only the salute and handshake clips (with
  horizontal hip travel removed), merges the meshes into a face and a body mesh, adds jaw
  and eye bones, a status light and a dark mouth interior, and generates facial
  blendshapes for lip-sync, blinking, brows and smiles.

The app shows `"Cool Man" by ardhanaputra · CC BY 4.0 · modified` in the control dock
whenever this character is active.

The model appears to be derived from a Ready Player Me avatar (its materials are named
`Wolf3D_*`). Review Ready Player Me's terms for their base assets before publishing the
app outside personal use.

## CMU Pronouncing Dictionary

`Resources/Speech/en_lexicon.txt` is a subset of CMUdict, Copyright (C) 1993-2015
Carnegie Mellon University, used under its BSD-style licence
(https://github.com/cmusphinx/cmudict/blob/master/LICENSE).
