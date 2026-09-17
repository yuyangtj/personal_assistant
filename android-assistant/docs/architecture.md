# Architecture

## Target runtime

```text
┌──────────────────────────────── Android application ────────────────────────────────┐
│                                                                                     │
│  Kotlin / Jetpack Compose                                                           │
│  ┌─────────────────┐   ┌────────────────────┐   ┌──────────────────────────────┐    │
│  │ Conversation UI │   │ Assistant client   │   │ Android integrations         │    │
│  │ Captions/control│   │ Tasks and events   │   │ Mic, notifications, functions│    │
│  └────────┬────────┘   └──────────┬─────────┘   └──────────────┬───────────────┘    │
│           │                       │                            │                    │
│           └──────────────┬────────┴────────────────────────────┘                    │
│                          ▼                                                          │
│              ┌────────────────────────┐                                             │
│              │ Assistant state store  │                                             │
│              │ StateFlow<UiState>     │                                             │
│              └────────────┬───────────┘                                             │
│                           │ AvatarCommand / AssistantResponse                        │
│                           ▼                                                          │
│              ┌────────────────────────┐                                             │
│              │ Unity avatar module    │                                             │
│              │ render/rig/animation   │                                             │
│              └────────────────────────┘                                             │
└───────────────────────────┬─────────────────────────────────────────────────────────┘
                            │ HTTPS initially; streaming later
                            ▼
                 ┌──────────────────────────┐
                 │ Personal Assistant API   │
                 │ tasks, events, approvals │
                 └──────────────────────────┘
```

## Implemented request flow (native shell + backend)

```text
Compose text field ──send──▶ AssistantSession
                               │ POST /tasks (external_source=android, external_key=uuid)
                               ▼
                    poll GET /tasks/{id}/events every 500 ms (new sequences only)
                               │
   TASK_PLANNING_STARTED/PLAN ─┼─▶ AvatarCommand THINKING · CURIOUS
   EXECUTION_STARTED ──────────┼─▶ AvatarCommand THINKING · NEUTRAL
   APPROVAL_REQUESTED ─────────┼─▶ Review card · exact SHA · open/approve/reject
                               │       └─ runtime token encrypted by Android Keystore
   ASSISTANT_REPLY ────────────┼─▶ POST /speech ─▶ cached WAV ─▶ Unity playback + lip-sync
                               │       └─ unavailable/too long ─▶ Android TTS fallback
   TASK_COMPLETED/FAILED/CANCELLED ─▶ stop polling
                               │
MiloHost.onSpeechFinished ─────┴─▶ SUCCESS (handshake) / ERROR / IDLE
```

- **Voice input** (`voice/VoiceInput.kt`): tapping the mic requests `RECORD_AUDIO`
  once, then starts Android `SpeechRecognizer` (on-device when available, otherwise
  the system service). While listening the avatar is `LISTENING · CURIOUS`, partial
  results fill the text field, and the mic ring follows the input level. The final
  transcript goes through the same `send()` path as typed text. The mic is disabled
  while a task runs or the avatar speaks, so his own voice is never captured.
- `MainActivity` calls `MiloHost.setEmbedded(true)` before Unity starts, so Unity
  hides its prototype HUD and auto-demo; Compose owns every control.
- Unity reports speech start and finish back through `MiloHost.Listener`.
- Settings choose the backend URL (default `http://127.0.0.1:8010` through
  `adb reverse`) and the character (`AvatarRuntime.SetCharacterName`).
- A normal task times out after 90 s; a task that reaches `APPROVAL_REQUESTED` keeps polling
  until approval/rejection produces a terminal event. Six consecutive polling failures, or
  a failed create call, produce a spoken local error.
- The active task ID and last handled event sequence are persisted. On process recreation,
  `GET /tasks/{id}/pending-approval` restores the typed review card before event polling
  resumes, so a long human review does not lose its exact-SHA approval gate.

## Avatar state contract

The native application sends high-level commands. Unity owns animation curves,
blendshape weights, gesture selection, and transition timing.

```kotlin
data class AvatarCommand(
    val mode: AvatarMode,
    val emotion: AvatarEmotion = AvatarEmotion.NEUTRAL,
    val intensity: Float = 0.5f,
    val speech: SpeechCue? = null,
)

enum class AvatarMode {
    IDLE,
    LISTENING,
    THINKING,
    SPEAKING,
    SUCCESS,
    ERROR,
}

enum class AvatarEmotion {
    NEUTRAL,
    WARM,
    CURIOUS,
    EXCITED,
    CONCERNED,
}
```

The assistant or language model may choose `mode`, `emotion`, and bounded
`intensity`. It must never set bone transforms or blendshape weights directly.

## Backend mapping

| Assistant event | Avatar mode | Default emotion |
| --- | --- | --- |
| Waiting for input | `IDLE` | `NEUTRAL` |
| Microphone active | `LISTENING` | `CURIOUS` |
| Task planning | `THINKING` | `CURIOUS` |
| Task executing | `THINKING` | `NEUTRAL` |
| Pull request awaiting review | `IDLE` | `CURIOUS` |
| Speech playback | `SPEAKING` | Response metadata |
| Task completed | `SUCCESS` | `WARM` |
| Task failed | `ERROR` | `CONCERNED` |

## Project boundaries

- Native Kotlin owns lifecycle, permissions, networking, durable state, and
  Android system integration.
- Unity owns only visual presentation and audio-aligned avatar animation.
- The backend owns task orchestration, tools, approvals, and persistent history.
- GitHub credentials never enter Android. A separately scoped approval token is provisioned
  at runtime, encrypted with an Android Keystore AES-GCM key, and sent only when the user
  taps Approve or Reject. Remote backends must use HTTPS; cleartext is restricted to local
  development hosts used through ADB reverse.
- Speech recognition and synthesis are replaceable adapters.
- Phone actions (timers, alarms, calendar drafts) are proposed by the backend,
  confirmed in native UI, and executed through public Android intents
  (`PhoneActionRunner`); Unity only reacts with speech and gestures.
- If the assistant is ever exposed as an AppFunction, that service calls native
  repositories and never communicates with Unity directly. AppFunctions let
  privileged system agents call this app; they do not let this app call others.

## Implemented assistant-response ingress

The provider boundary now stops before Unity:

```text
Kimi / MiniMax / deterministic mock
                    |
                    v
         Android assistant client
                    | POST /speech (optional Gemini TTS)
                    v
       app-private 24 kHz WAV cache
                    | AssistantResponse JSON + audioPath
                    v
          MiloAssistantBridge
                    | UnitySendMessage
                    v
     ApplyAssistantResponseJson
                    |
          validate + deduplicate
                    v
       SpeakEnglish -> cloud WAV or Android TTS fallback
                    |
                    v
          audio-clock-aligned visemes
```

Unity does not know which text model generated the reply. It receives only a validated
app-private audio path, never credentials or provider controls. The response ID is used
to prevent polling or delivery retries from speaking the same response twice.
The in-memory check protects the current Unity process; durable event cursors
remain the future Kotlin client's responsibility.
