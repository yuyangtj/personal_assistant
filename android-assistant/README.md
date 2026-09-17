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
- The assistant can set timers and alarms and draft calendar events on the phone.
  Each action runs only after the user confirms it (see below).
- Coding tasks now surface a pull-request review card. The app opens the exact GitHub PR,
  displays the reviewed commit SHA, and can approve or reject through the backend gate.
  The approval token is entered at runtime and encrypted with Android Keystore; it is never
  compiled into the APK or logged.

## Build sequence

1. ~~Run the Unity prototype on a real Android phone.~~
2. ~~Produce and integrate a detailed rigged character.~~
3. ~~Validate expressions and English TTS lip-sync on-device.~~
4. ~~Define and test the provider-neutral assistant-response ingress.~~
5. ~~Scaffold the Kotlin/Compose controller and embed Unity as a library.~~
6. ~~Connect task creation and event polling to the Personal Assistant backend.~~
7. ~~Add microphone and speech recognition.~~
8. ~~Confirmed on-phone actions: timers, alarms, and calendar event drafts through public
   Android intents.~~
9. ~~GitHub pull-request review and approval controls backed by exact-SHA verification.~~
10. Optionally expose the assistant as an Android AppFunction (for example
   `askAssistant`), so approved system agents can call it. AppFunctions work in that
   direction only: calling other apps' functions requires the privileged
   `EXECUTE_APP_FUNCTIONS` permission, which ordinary apps cannot hold, and the
   platform feature is still experimental on Android 16+.

The visual spike is deliberately first. Backend and voice integration should
not hide a character or rig that fails the quality bar.

## Run the native app with the backend

1. Start the backend from the repository root. The alternate host ports avoid clashes with
   other local services:

   ```shell
   ASSISTANT_API_PORT=8010 ASSISTANT_POSTGRES_PORT=55433 docker compose up --build -d
   ```

2. Build the native app. The script re-exports Unity when its sources changed, or pass
   `--reexport`:

   ```shell
   zsh tools/build_native_android.sh
   adb install -r native/app/build/outputs/apk/debug/app-debug.apk
   ```

3. Let the phone reach the Mac backend over USB, then launch the app:

   ```shell
   adb reverse tcp:8010 tcp:8010
   adb shell am start -n com.personalassistant.avatar.shell/.MainActivity
   ```

4. Open **Settings** in the app and paste `ASSISTANT_APPROVAL_TOKEN` into **Approval
   token**, then tap **Save**. The value is encrypted using a non-exportable Android
   Keystore key and stored only in app-private data. To copy the configured value on the
   Mac without printing it:

   ```shell
   printf '%s' "$ASSISTANT_APPROVAL_TOKEN" | pbcopy
   ```

   Use the USB loopback above for local HTTP development. A backend reached over a network
   must use HTTPS before provisioning this credential.

### Pull-request approvals

When a coding task creates a PR, polling no longer expires after 90 seconds. Instead, the
app enters **Review needed** and shows the repository, PR number, exact commit SHA, and
three controls. The active task ID and event cursor are persisted, and the typed
`pending-approval` endpoint restores the card if Android recreates the app:

- **Open GitHub** opens the validated `https://github.com/...` PR for review and for marking
  a draft ready.
- **Approve** sends the exact displayed SHA to the protected backend approval endpoint. The
  backend checks the live PR state and refuses a draft, closed, or changed PR.
- **Reject** cancels the task without merging; the PR and branch remain available for
  inspection.

The GitHub credential remains exclusively on the backend. The Android app holds only the
separate approval credential, encrypted at rest, and sends it only to the configured
assistant backend.

### Phone actions

When a request clearly asks for one, Kimi proposes a single structured action. The backend
validates it (anything unsupported or malformed is dropped) and attaches it to the
`ASSISTANT_REPLY`. The app shows a **Confirm action** card and runs the action only after
you tap **Confirm**:

| Action | Android API | Result |
| --- | --- | --- |
| `set_timer` | `AlarmClock.ACTION_SET_TIMER` (skip UI) | Timer starts in the Clock app |
| `set_alarm` | `AlarmClock.ACTION_SET_ALARM` (skip UI, validated date or repeat days) | Alarm is set in the Clock app |
| `create_event` | `Intent.ACTION_INSERT` on `CalendarContract.Events` | Calendar opens pre-filled; you tap Save |

- **Not now** runs nothing.
- Either way, the outcome is added to the task as a user message.
- Requests are sent with the phone's local time and time zone, so "tomorrow at noon"
  resolves correctly.
- A one-time alarm is accepted only when its requested date is the next occurrence of
  its clock time; later dated alarms are rejected because the public Clock intent cannot
  preserve an arbitrary date.
- Reading calendars, email, or messages is deliberately not supported: it would send
  personal data to the language model provider.

Verified on the Pixel 10 Pro XL:

- a 2-minute timer started in Clock;
- "Lunch with Anna tomorrow at noon" opened Calendar for Tue 15 Sept 12:00–13:00;
- **Not now** on an alarm produced no Clock call.

Each app launch starts a conversation (`source_context.conversation_id`), so follow-up
questions keep their context. With `KIMI_API_KEY` set on the backend, replies are real
answers from Kimi; otherwise the fake executor replies.

Type a request, or tap the microphone and speak. While you talk he listens and your
words appear live; the finished sentence is sent like a typed request. He thinks while
the task runs, speaks the backend's `ASSISTANT_REPLY`, and shakes hands when it
completes.

Speech recognition runs on-device when an English model is installed, and otherwise
falls back to the system recognizer. Microphone permission is requested the first
time you tap the mic.

Debug builds also accept `--es prompt '<text>'` (send a request) and `--ez listen true`
(open the microphone) for scripted checks.

Verified on a Pixel 10 Pro XL (Android 17):

- a request completed and was spoken with lip-sync;
- cancelling a slow task spoke the cancellation reply;
- with the API stopped, he spoke a local offline message;
- switching characters in Settings worked;
- the screen recording averaged 59.8 fps.

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
