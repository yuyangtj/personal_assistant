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
   ASSISTANT_REPLY ────────────┼─▶ AssistantResponse ─▶ Unity TTS + lip-sync
   TASK_COMPLETED/FAILED/CANCELLED ─▶ stop polling
                               │
MiloHost.onSpeechFinished ─────┴─▶ SUCCESS (handshake) / ERROR / IDLE
```

- `MainActivity` calls `MiloHost.setEmbedded(true)` before Unity starts, so Unity
  hides its prototype HUD and auto-demo; Compose owns every control.
- Unity reports speech start and finish back through `MiloHost.Listener`.
- Settings choose the backend URL (default `http://127.0.0.1:8010` through
  `adb reverse`) and the character (`AvatarRuntime.SetCharacterName`).
- A task times out after 90 s; six consecutive polling failures, or a failed
  create call, produce a spoken local error.

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
| Speech playback | `SPEAKING` | Response metadata |
| Task completed | `SUCCESS` | `WARM` |
| Task failed | `ERROR` | `CONCERNED` |

## Project boundaries

- Native Kotlin owns lifecycle, permissions, networking, durable state, and
  Android system integration.
- Unity owns only visual presentation and audio-aligned avatar animation.
- The backend owns task orchestration, tools, approvals, and persistent history.
- Speech recognition and synthesis are replaceable adapters.
- AppFunctions call native repositories and never communicate with Unity
  directly.

## Implemented assistant-response ingress

The provider boundary now stops before Unity:

```text
Kimi / another model / deterministic mock
                    |
                    v
         Android assistant client
                    | AssistantResponse JSON
                    v
          MiloAssistantBridge
                    | UnitySendMessage
                    v
     ApplyAssistantResponseJson
                    |
          validate + deduplicate
                    v
       SpeakEnglish -> Android TTS
                    |
                    v
          range-aligned visemes
```

Unity does not know which provider generated the reply. The response ID is used
to prevent polling or delivery retries from speaking the same response twice.
The in-memory check protects the current Unity process; durable event cursors
remain the future Kotlin client's responsibility.
