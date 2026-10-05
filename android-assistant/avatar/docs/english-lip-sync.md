# English TTS Lip Sync

## Runtime flow

```text
AssistantResponse text + optional cloud WAV
        |
        v
SpeakEnglish(text) ── mouth rests while audio is prepared
        |
        ├── cloud WAV: use measured duration + loudness envelope
        |
        └── Android TextToSpeech.synthesizeToFile (on-device US-English voice)
        |   records every word range + its exact audio frame
        v
MiloTextToSpeech (Java): read WAV → 60 Hz loudness envelope
        |
        ├── OnTtsTimeline {words:[start,end,frame], envelope, durationMs}
        |         |
        |         v
        |   EnglishPronouncer: text → ARPAbet phones (CMUdict subset + spelling rules)
        |   EnglishVisemePlanner: phones fitted inside each word's measured audio window
        |
        └── AudioTrack playback → OnTtsStarted / OnTtsDone
                  |
                  v
        Unity polls getPlaybackPositionMs() (AudioTimestamp presentation clock)
                  |
                  v
        VisemeMixer: coarticulated weights, sealed closures, loudness-driven jaw
                  |
                  v
        Milo_Face: 15 viseme blendshapes + jawOpen, layered with expressions
```

The response text is still the single input. Speech is synthesized to a file
before playback so the timeline is known up front: each word's audio start is
the frame reported by the TTS engine, and the mouth follows the audio device's
presentation clock rather than an estimate started when the request was sent.

Gemini audio does not currently include word or phoneme timestamps. For that path, the
same English pronunciation plan is stretched to the WAV's measured duration and the
60 Hz envelope drives the jaw. Local Android TTS remains slightly more precise at word
boundaries because it supplies range callbacks; both paths use the real playback clock.

## Accuracy

- **Pronunciation, not spelling.** About 27,000 common English words plus
  assistant vocabulary come from CMUdict (`Resources/Speech/en_lexicon.txt`).
  "phone" is `F OW N` → FF, oh, ou, nn; "knight" has no K. Unknown names use
  spelling rules; numbers are spoken as words; possessives and plurals reuse the
  base entry.
- **No drift.** Every sound is placed inside its word's measured window, so a
  sentence cannot slide out of sync. Silence cues land where the loudness
  envelope drops to zero.
- **Readable closures.** p/b/m, f/v and th always reach at least 85 % weight at
  their midpoint, even when adjacent to another closure ("five big").
- **Natural blending.** Each sound anticipates by ~55 ms and releases over
  ~75 ms. Vowel jaw drop is scaled by the audio loudness.
- **Expressions stay layered.** Smile and frown are capped while speaking so
  they do not overpower the visemes.

Remaining approximation: sounds within a word are distributed by typical
duration rather than aligned per phoneme, and a word missing from the lexicon
falls back to spelling rules. A cloud voice returning per-phoneme viseme
timestamps would remove both.

## Voices

Each character profile sets a voice style, which is passed through `AndroidTextToSpeech.Speak`:

- **Cool Man uses `male`.** The bridge picks the first installed offline US-English
  male Google voice from `en-us-x-iom-local`, `en-us-x-tpd-local`, `en-us-x-iol-local`
  (then any voice named "male"), at pitch 0.96.
  - If no male voice is installed, it lowers the default voice's pitch to 0.78.
- **Milo uses `default`,** the engine's default voice at pitch 1.02.

Every synthesized utterance logs `TTS_PITCH voice=… medianF0Hz=…`, an autocorrelation
estimate. On the Pixel 10 Pro XL, Cool Man (`en-us-x-iom-local`) measures 132–138 Hz,
in the typical male range; Milo's default voice measures 240 Hz.

## Engine notes

- Google TTS on Android 17 delivers `onRangeStart` values as
  `(frame, start, end)` instead of the documented `(start, end, frame)`.
  `MiloTextToSpeech` detects the rotation (a character range always has
  `start < end`) and normalizes it.
- If synthesis to a file fails or the WAV is unreadable, the bridge logs
  `TTS_LIVE_FALLBACK` and speaks live; Unity then re-anchors an estimated
  timeline on each range callback.
- If the engine reports no ranges, the estimated timeline is stretched to the
  real audio duration.

## Validation

- `PrototypeProjectSetup.ValidateSpeech` checks pronunciation, word-window
  fitting, closure strength, and jaw response (`SPEECH_VALIDATION_PASSED`).
- Development builds log `TTS_TIMELINE` once per utterance and `TTS_CLOCK`
  five times per second with the audio time, dominant viseme, jaw and loudness.
- Pixel 10 Pro XL, Android 17: 9 word ranges and a 198-sample envelope for
  "Hello! I'm Milo. How can I help you today?". Synthesis adds about 0.5 s
  before audio. A 60 fps screen recording of "My mom bought five big bubbles
  from Milo." shows the lips sealing on m/b, the upper teeth on the lower lip
  for "five", and rounded lips on "Milo", with no dropped frames.

Android API references:

- [TextToSpeech.synthesizeToFile](https://developer.android.com/reference/android/speech/tts/TextToSpeech#synthesizeToFile(java.lang.CharSequence,%20android.os.Bundle,%20java.io.File,%20java.lang.String))
- [UtteranceProgressListener.onRangeStart](https://developer.android.com/reference/android/speech/tts/UtteranceProgressListener#onRangeStart(java.lang.String,%20int,%20int,%20int))
- [AudioTrack.getTimestamp](https://developer.android.com/reference/android/media/AudioTrack#getTimestamp(android.media.AudioTimestamp))
