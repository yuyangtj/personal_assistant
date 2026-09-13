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

The current Personal Assistant manager-model adapter produces `TaskAnalysis`
for capability selection, not user-facing prose. A later backend event or reply
endpoint should produce the text and a durable event ID; the Kotlin client will
map those fields into this envelope. Kimi credentials are not required until
that provider client is connected.
