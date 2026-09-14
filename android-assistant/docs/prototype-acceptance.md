# Visual Prototype Acceptance

The first prototype validates character quality before backend or conversational
integration.

## Required demonstration

One full-screen Unity scene running on an Android phone must show:

1. The approved character under final-style lighting.
2. Natural idle breathing, blinking, and small gaze changes.
3. Smooth transitions between idle, listening, thinking, and speaking.
4. Neutral, warm, curious, excited, and concerned expressions.
5. One prerecorded sentence with a deterministic viseme timeline.
6. A speaking gesture layered with facial animation and lip-sync.
7. Success and error reactions returning cleanly to idle.

## Quality gates

- The character is recognizable and readable at normal phone distance.
- Mouth shapes are clearly distinct without looking exaggerated at rest.
- Lip-sync does not visibly drift over the complete sentence.
- Expression changes do not interrupt or collapse speech shapes.
- Blinks close completely and reopen without mesh intersections.
- Eye motion feels attentive rather than fixed or erratic.
- State transitions do not pop, reset the pose, or snap the head.
- The scene sustains 60 frames per second on the chosen target phone, or a
  documented stable 30 frames per second on lower-end hardware.
- No missing materials, shader errors, animation-binding warnings, or crashes.

## Deferred from this spike

- Live speech recognition
- Live language-model responses
- Network connectivity
- On-device actions (since implemented as confirmed timer, alarm, and calendar intents)
- Android AppFunctions (exposing the assistant to privileged system agents)
- Background execution
- Notifications
- Multiple characters or outfits
- Emotion learning or personalization

Passing this spike authorizes native Android integration. Failing it sends the
work back to character modeling, rigging, lighting, or animation rather than
adding application features around an unsatisfactory avatar.
