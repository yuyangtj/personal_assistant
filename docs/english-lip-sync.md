# English TTS Lip Sync

## Runtime flow

```text
Speaking button / future assistant reply
                 |
                 v
        SpeakEnglish(text)
          /             \
         v               v
EnglishVisemePlanner   Android TextToSpeech
  spelling -> cues      audible English audio
         |               |
         |        start / range / done
         +---------> re-anchor timeline
                         |
                         v
              Milo's 14 viseme blendshapes
```

The response text is the shared input for audio and animation. Android's TTS
engine reports when the utterance starts, which character range is about to be
spoken, and when it completes. Each range event resets the estimated mouth
timeline to that word's character position, preventing drift across the full
sentence.

## Current accuracy

This is real, English-oriented audio synchronization:

- Speech audio comes from the phone's installed US-English Android TTS voice.
- Word/range timing comes from the TTS engine rather than a fixed animation.
- The rig selects among fourteen speech shapes plus its silent/rest shape.
- Completion returns the avatar to Idle instead of repeating the mouth loop.

Inside each reported range, individual mouth shapes are inferred from common
English letter patterns. This is a good interactive-assistant baseline, but it
is not exact phoneme alignment: unusual pronunciations, homographs, names, and
accent-specific sounds can differ from their spelling.

## Production upgrade path

Keep `SpeakEnglish(text)` as the public avatar boundary, but replace the local
planner input with phoneme or viseme timestamps returned alongside generated
audio. That will make every consonant and vowel use the synthesizer's actual
pronunciation. Kimi can supply assistant response text later; it does not need
to own speech timing or know about Unity blendshapes.

Android API references:

- [TextToSpeech](https://developer.android.com/reference/android/speech/tts/TextToSpeech)
- [UtteranceProgressListener](https://developer.android.com/reference/android/speech/tts/UtteranceProgressListener)
