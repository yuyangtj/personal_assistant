# The Live2D avatar

`entry.ts` is our side of the console's Live2D avatar: it mounts the Live2D Cubism SDK for
Web's sample model renderer into the console's avatar stage and lets `app/web/static/avatar.js`
drive the mouth (lip sync), gaze, head and expression.

Live2D's SDK, its sample code and the models are Live2D's, under their licences
(Proprietary Software, Open Software, Free Material), so none of them are in this repository.
`scripts/build_live2d.sh` builds against the SDK and the model you download, in a temporary
folder, and writes everything the server needs into one folder. See "The Live2D avatar" in
`docs/remote-hosting.md`.
