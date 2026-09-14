# Assistant Response Contract

## Purpose

This is the stable boundary between the future native assistant client and the
Unity avatar. It deliberately contains presentation data, not model-provider
details, task-routing decisions, audio timing, bones, or blendshape weights.

```json
{
  "responseId": "event-018f2f",
  "text": "Hello! I'm Milo. How can I help you today?",
  "emotion": "Excited",
  "intensity": 0.72
}
```

| Field | Requirement |
| --- | --- |
| `responseId` | Required stable event or message ID. Recent repeated IDs are ignored. |
| `text` | Required, non-blank English speech text; maximum 4,000 characters. |
| `emotion` | Optional avatar emotion name; unknown values fall back to `Warm`. |
| `intensity` | Optional `0.0`–`1.0` presentation strength; runtime clamps it. |

## Android delivery

The Unity Android plug-in exposes either of these Java calls to a future Kotlin
host:

```kotlin
MiloAssistantBridge.deliverResponse(responseJson)
MiloAssistantBridge.deliverResponse("AvatarRuntime", responseJson)
```

The bridge uses `UnitySendMessage` to call
`AvatarRuntime.ApplyAssistantResponseJson`. Unity validates the envelope,
suppresses a recently repeated `responseId`, selects the requested emotion, and passes
the text into the existing English TTS/viseme pipeline.

The current Speaking button constructs a mock envelope and routes it through
the same Java bridge on Android. In the Unity Editor it falls back to a direct
method call, so validation remains credential-free.

## Backend relationship

The backend appends an `ASSISTANT_REPLY` event to every task that finishes,
immediately before its terminal event:

```json
{"text": "All done. I handled your request: Plan my morning",
 "emotion": "Warm", "intensity": 0.7, "outcome": "completed"}
```

| Outcome | Emotion | Text source |
| --- | --- | --- |
| `completed` | `Warm` | The executor's `reply` output, falling back to its `summary` |
| `failed` | `Concerned` | A safe user-facing sentence; raw errors are never spoken |
| `cancelled` | `Neutral` | "Okay, I've stopped working on that." |

The native client (`AssistantSession.kt`) maps this event to the envelope, using
`responseId = "task:<task id>:<event sequence>"` so re-polling never repeats
speech. When the connection fails, the client speaks a local `Concerned`
message with a `local:<timestamp>` ID.
