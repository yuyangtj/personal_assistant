using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;

namespace PersonalAssistant.Avatar
{
    /// <summary>
    /// Drives the assistant character: the realistic Cool Man or the cartoon Milo rig (see
    /// <see cref="AvatarCharacterProfile"/>), falling back to a primitive stand-in. Handles
    /// state transitions, gestures, gaze, blinking, expressions, and English speech visemes
    /// synchronized to the Android TTS audio clock.
    /// </summary>
    public sealed class ProceduralAvatarController : MonoBehaviour
    {
        public AvatarMode Mode { get; private set; } = AvatarMode.Idle;
        public AvatarEmotion Emotion { get; private set; } = AvatarEmotion.Neutral;
        public float Intensity { get; private set; } = 0.5f;
        public string ActiveViseme { get; private set; } = "sil";
        public string ActiveSpeechText { get; private set; } = string.Empty;
        public bool IsEnglishSpeechActive => ttsTimelineActive;
        public string LastAssistantResponseId { get; private set; } = string.Empty;
        public AvatarCharacterProfile ActiveProfile { get; private set; }

        private const string CharacterPreferenceKey = "assistant.character";

        private readonly Dictionary<string, Material> materials = new();
        private Transform avatarRoot;
        private Transform body;
        private Transform head;
        private Transform leftEye;
        private Transform rightEye;
        private Transform leftPupil;
        private Transform rightPupil;
        private Transform leftLid;
        private Transform rightLid;
        private Transform leftBrow;
        private Transform rightBrow;
        private Transform mouth;
        private Transform leftMouthCorner;
        private Transform rightMouthCorner;
        private Transform leftCheek;
        private Transform rightCheek;
        private Transform leftArm;
        private Transform rightArm;
        private Transform statusOrb;
        private bool usingRiggedAsset;
        private SkinnedMeshRenderer riggedFace;
        private Vector3 leftEyeBaseInHead;
        private Vector3 rightEyeBaseInHead;
        private readonly int[] visemeShapeIndices = new int[VisemeMixer.Visemes.Length];
        private readonly Dictionary<string, int> faceShapeIndices = new();
        private float[] faceShapeWeights = Array.Empty<float>();

        private Vector3 bodyBasePosition;
        private Vector3 headBasePosition;
        private Quaternion headBaseRotation;
        private Quaternion leftArmBaseRotation;
        private Quaternion rightArmBaseRotation;
        private Vector3 statusOrbBaseScale;
        private Transform characterRoot;
        private UnityEngine.Animation gestureAnimation;
        private float gestureEndsAt;
        private bool greetingPlayed;
        private float blinkAmount;
        private float blinkVelocity;
        private float nextBlinkAt;
        private float stateChangedAt;
        private float speechStartedAt;
        private float speechTimelineDuration;
        private float speechDeadlineAt;
        private bool ttsTimelineActive;
        private string activeUtteranceId;
        private List<EnglishVisemeCue> englishSpeechCues = new();
        private readonly VisemeMixer visemeMixer = new();
        private List<EnglishVisemeCue> demoSpeechCues;
        private float demoSpeechDuration;
        private bool audioTimelineReceived;
        private bool awaitingSpeechAudio;
        private bool audioClockActive;
        private float speechTime;
        private float nextSpeechLogAt;
        private int[] speechEnvelope;
        private float speechEnvelopeRate;
        private readonly HashSet<string> handledAssistantResponseIds = new();
        private readonly Queue<string> handledAssistantResponseOrder = new();
        private Color statusColor = new(0.20f, 0.80f, 0.75f);
        private Vector2 gaze;
        private Vector2 gazeTarget;
        private float nextGazeAt;

        private const string DemoSpeechLine = "Hello! I'm Milo. How can I help you today?";

        private void Awake()
        {
            EnsureBuilt();
            nextBlinkAt = Time.time + 1.4f;
            nextGazeAt = Time.time + 0.8f;
            ApplyCommand(AvatarMode.Idle, AvatarEmotion.Warm, 0.45f);
        }

        public void BuildForPreview()
        {
            EnsureBuilt();
            ApplyCommand(AvatarMode.Idle, AvatarEmotion.Warm, 0.45f);
        }

        private void EnsureBuilt()
        {
            if (avatarRoot != null) return;
            BuildAvatar(PreferredCharacter());
            bodyBasePosition = body.localPosition;
            headBasePosition = head.localPosition;
            headBaseRotation = head.localRotation;
            leftArmBaseRotation = leftArm.localRotation;
            rightArmBaseRotation = rightArm.localRotation;
            statusOrbBaseScale = statusOrb.localScale;
            if (usingRiggedAsset) CaptureRiggedDefaults();
            ApplyCameraFraming();
        }

        /// <summary>UnitySendMessage entry point for native hosts: "CoolMan" or "Milo".</summary>
        public void SetCharacterName(string value)
        {
            if (!Enum.TryParse(value, true, out AvatarCharacter character)) return;
            if (ActiveProfile != null && ActiveProfile.Character == character) return;
            SetCharacter(character);
        }

        /// <summary>Rebuilds the avatar as another character; the choice persists on device.</summary>
        public void SetCharacter(AvatarCharacter character, bool remember = true)
        {
            if (remember)
            {
                PlayerPrefs.SetInt(CharacterPreferenceKey, (int)character);
                PlayerPrefs.Save();
            }
            if (avatarRoot != null)
            {
                if (Application.isPlaying) Destroy(avatarRoot.gameObject);
                else DestroyImmediate(avatarRoot.gameObject);
            }
            avatarRoot = null;
            usingRiggedAsset = false;
            riggedFace = null;
            characterRoot = null;
            gestureAnimation = null;
            gestureEndsAt = 0f;
            greetingPlayed = false;
            ActiveProfile = null;
            faceShapeIndices.Clear();
            faceShapeWeights = Array.Empty<float>();
            preferredCharacterOverride = character;
            EnsureBuilt();
        }

        private AvatarCharacter? preferredCharacterOverride;

        private AvatarCharacter PreferredCharacter()
        {
            if (preferredCharacterOverride.HasValue) return preferredCharacterOverride.Value;
            int stored = PlayerPrefs.GetInt(CharacterPreferenceKey, (int)AvatarCharacter.CoolMan);
            return Enum.IsDefined(typeof(AvatarCharacter), stored) ? (AvatarCharacter)stored : AvatarCharacter.CoolMan;
        }

        private void ApplyCameraFraming()
        {
            AvatarCharacterProfile profile = ActiveProfile ?? AvatarCharacterProfile.Milo;
            Camera camera = Camera.main;
            if (camera == null) return;
            camera.transform.position = transform.TransformPoint(profile.CameraPosition);
            camera.transform.LookAt(transform.TransformPoint(profile.CameraTarget));
            camera.fieldOfView = profile.CameraFieldOfView;
            camera.nearClipPlane = profile.Character == AvatarCharacter.CoolMan ? 0.05f : 0.3f;
        }

        private void Update()
        {
            float t = Time.time;
            UpdateBlink(t);
            UpdateGaze(t);
            if (usingRiggedAsset && !greetingPlayed && Application.isPlaying)
            {
                greetingPlayed = true;
                PlayGesture(ActiveProfile.GreetingClip);
            }
            if (!IsGesturePlaying(t)) UpdateBody(t);
            SampleSpeech(t);
            UpdateFace(t);
            UpdateStatusOrb(t);
            if (ttsTimelineActive && t >= speechDeadlineAt) FinishEnglishSpeech("timeout");
        }

        public void ApplyCommand(AvatarMode mode, AvatarEmotion emotion, float intensity = 0.5f)
        {
            if (mode != AvatarMode.Speaking && ttsTimelineActive)
            {
                ttsTimelineActive = false;
                audioTimelineReceived = false;
                audioClockActive = false;
                speechEnvelope = null;
                activeUtteranceId = null;
                ActiveSpeechText = string.Empty;
                AndroidTextToSpeech.Stop();
                AndroidHost.NotifySpeechFinished(LastAssistantResponseId, "interrupted");
            }

            if (Mode != mode)
            {
                stateChangedAt = Time.time;
                if (mode == AvatarMode.Success && ActiveProfile != null) PlayGesture(ActiveProfile.SuccessClip);
                if (mode == AvatarMode.Speaking)
                {
                    speechStartedAt = Time.time;
                }
            }

            Mode = mode;
            Emotion = emotion;
            Intensity = Mathf.Clamp01(intensity);
        }

        public void SpeakEnglish(string text)
        {
            SpeakEnglish(text, AvatarEmotion.Excited, 0.72f);
        }

        private void SpeakEnglish(string text, AvatarEmotion emotion, float intensity)
        {
            SpeakEnglish(text, emotion, intensity, null);
        }

        private void SpeakEnglish(string text, AvatarEmotion emotion, float intensity, string audioPath)
        {
            if (string.IsNullOrWhiteSpace(text)) return;
            englishSpeechCues = EnglishVisemePlanner.Build(text, out speechTimelineDuration);
            ActiveSpeechText = text.Trim();
            activeUtteranceId = null;
            audioTimelineReceived = false;
            audioClockActive = false;
            speechEnvelope = null;
            speechTime = 0f;
            ttsTimelineActive = true;
            speechStartedAt = Time.time;
            speechDeadlineAt = Time.time + speechTimelineDuration + 2f;
            ApplyCommand(AvatarMode.Speaking, emotion, intensity);

            string voiceStyle = ActiveProfile?.VoiceStyle ?? "default";
            bool nativeSpeechRequested = !string.IsNullOrWhiteSpace(audioPath)
                ? AndroidTextToSpeech.SpeakWave(gameObject, ActiveSpeechText, audioPath, voiceStyle)
                : AndroidTextToSpeech.Speak(gameObject, ActiveSpeechText, voiceStyle);
            // The mouth rests while Android synthesizes; the timeline starts with the audio.
            awaitingSpeechAudio = nativeSpeechRequested;
            Debug.Log($"TTS_REQUESTED: chars={ActiveSpeechText.Length}, cues={englishSpeechCues.Count}, estimatedSeconds={speechTimelineDuration:F2}, native={nativeSpeechRequested}");
        }

        public void ApplyAssistantResponseJson(string json)
        {
            AssistantResponse response;
            try
            {
                response = JsonUtility.FromJson<AssistantResponse>(json);
            }
            catch (Exception exception)
            {
                RejectAssistantResponse($"invalid JSON ({exception.GetType().Name})");
                return;
            }

            if (response == null || string.IsNullOrWhiteSpace(response.text))
            {
                RejectAssistantResponse("text is required");
                return;
            }
            if (string.IsNullOrWhiteSpace(response.responseId))
            {
                RejectAssistantResponse("responseId is required");
                return;
            }
            if (response.text.Length > 4000)
            {
                RejectAssistantResponse("text exceeds the 4000-character TTS limit");
                return;
            }
            if (handledAssistantResponseIds.Contains(response.responseId))
            {
                Debug.Log($"ASSISTANT_RESPONSE_DUPLICATE: {response.responseId}");
                return;
            }

            if (!Enum.TryParse(response.emotion, true, out AvatarEmotion emotion)) emotion = AvatarEmotion.Warm;
            float intensity = float.IsNaN(response.intensity) || float.IsInfinity(response.intensity) ? 0.65f : Mathf.Clamp01(response.intensity);
            RememberAssistantResponse(response.responseId);
            GetComponent<PrototypeDemo>()?.DisableAutoDemo();
            Debug.Log($"ASSISTANT_RESPONSE_ACCEPTED: id={LastAssistantResponseId}, chars={response.text.Length}, emotion={emotion}");
            SpeakEnglish(response.text, emotion, intensity, response.audioPath);
        }

        private void RememberAssistantResponse(string responseId)
        {
            const int historyLimit = 128;
            LastAssistantResponseId = responseId;
            handledAssistantResponseIds.Add(responseId);
            handledAssistantResponseOrder.Enqueue(responseId);
            while (handledAssistantResponseOrder.Count > historyLimit)
                handledAssistantResponseIds.Remove(handledAssistantResponseOrder.Dequeue());
        }

        private void RejectAssistantResponse(string reason)
        {
            Debug.LogWarning($"ASSISTANT_RESPONSE_REJECTED: {reason}");
            ApplyCommand(AvatarMode.Error, AvatarEmotion.Concerned, 0.65f);
        }

        /// <summary>Exact word audio frames and loudness from speech synthesized before playback.</summary>
        public void OnTtsTimeline(string json)
        {
            if (!ttsTimelineActive) return;
            TtsTimelineEvent timeline;
            try
            {
                timeline = JsonUtility.FromJson<TtsTimelineEvent>(json);
            }
            catch (Exception exception)
            {
                Debug.LogWarning($"TTS_TIMELINE_INVALID: {exception.GetType().Name}");
                return;
            }
            if (timeline == null || timeline.sampleRate <= 0) return;

            List<TtsWordTiming> timings = new();
            if (timeline.words != null)
                foreach (TtsRangeEvent word in timeline.words)
                    timings.Add(new TtsWordTiming(word.start, word.end, word.frame / (float)timeline.sampleRate));

            speechTimelineDuration = timeline.durationMs / 1000f;
            englishSpeechCues = EnglishVisemePlanner.Build(ActiveSpeechText, timings, speechTimelineDuration);
            speechEnvelope = timeline.envelope;
            speechEnvelopeRate = timeline.envelopeRate;
            activeUtteranceId = timeline.utteranceId;
            audioTimelineReceived = true;
            speechDeadlineAt = Time.time + speechTimelineDuration + 3f;
            Debug.Log($"TTS_TIMELINE: words={timings.Count}, envelope={(speechEnvelope == null ? 0 : speechEnvelope.Length)}, cues={englishSpeechCues.Count}, durationMs={timeline.durationMs}");
        }

        public void OnTtsStarted(string utteranceId)
        {
            if (!ttsTimelineActive) return;
            activeUtteranceId = utteranceId;
            awaitingSpeechAudio = false;
            speechStartedAt = Time.time;
            speechTime = 0f;
            audioClockActive = audioTimelineReceived;
            speechDeadlineAt = Time.time + speechTimelineDuration + 2f;
            AndroidHost.NotifySpeechStarted(LastAssistantResponseId);
            Debug.Log($"TTS_STARTED: {utteranceId}, audioClock={audioClockActive}");
        }

        public void OnTtsRange(string json)
        {
            if (!ttsTimelineActive) return;
            TtsRangeEvent range = JsonUtility.FromJson<TtsRangeEvent>(json);
            if (range == null) return;
            float plannedTime = EnglishVisemePlanner.FindTimeForCharacter(englishSpeechCues, range.start);
            speechStartedAt = Time.time - plannedTime;
            speechDeadlineAt = Time.time + Mathf.Max(1f, speechTimelineDuration - plannedTime + 1.2f);
            Debug.Log($"TTS_RANGE: start={range.start}, end={range.end}, frame={range.frame}, cueTime={plannedTime:F2}");
        }

        public void OnTtsDone(string utteranceId)
        {
            if (!ttsTimelineActive) return;
            if (!string.IsNullOrEmpty(activeUtteranceId) && utteranceId != activeUtteranceId) return;
            FinishEnglishSpeech("done");
        }

        public void OnTtsError(string detail)
        {
            if (!ttsTimelineActive) return;
            Debug.LogWarning($"TTS_ERROR: {detail}");
            FinishEnglishSpeech("error");
        }

        public void ApplyCommandJson(string json)
        {
            AvatarCommand command = JsonUtility.FromJson<AvatarCommand>(json);
            if (command == null)
            {
                Debug.LogWarning("Avatar command JSON was empty.");
                return;
            }

            if (!Enum.TryParse(command.mode, true, out AvatarMode mode)) mode = AvatarMode.Idle;
            if (!Enum.TryParse(command.emotion, true, out AvatarEmotion emotion)) emotion = AvatarEmotion.Neutral;
            ApplyCommand(mode, emotion, command.intensity);
        }

        public void SetMode(string value)
        {
            if (Enum.TryParse(value, true, out AvatarMode mode)) ApplyCommand(mode, Emotion, Intensity);
        }

        public void SetEmotion(string value)
        {
            if (Enum.TryParse(value, true, out AvatarEmotion emotion)) ApplyCommand(Mode, emotion, Intensity);
        }

        private bool IsGesturePlaying(float now)
        {
            return gestureAnimation != null && now < gestureEndsAt;
        }

        private void PlayGesture(string clipName)
        {
            if (gestureAnimation == null || string.IsNullOrEmpty(clipName) || !Application.isPlaying) return;
            AnimationClip clip = FindGestureClip(clipName);
            if (clip == null) return;
            gestureAnimation.CrossFade(clip.name, 0.25f);
            gestureEndsAt = Time.time + clip.length;
            Debug.Log($"GESTURE_PLAYING: {clip.name} ({clip.length:F2}s)");
        }

        private AnimationClip FindGestureClip(string clipName)
        {
            if (gestureAnimation == null) return null;
            foreach (AnimationState state in gestureAnimation)
                if (state.clip != null && state.clip.name.EndsWith(clipName, StringComparison.OrdinalIgnoreCase)) return state.clip;
            return null;
        }

        private void UpdateBody(float t)
        {
            if (ActiveProfile != null && ActiveProfile.Gestures == GestureSpace.Character)
            {
                UpdateCharacterSpaceBody(t);
                return;
            }

            float stateTime = t - stateChangedAt;
            float breathe = Mathf.Sin(t * 1.65f) * 0.018f;
            float transition = Smooth01(stateTime / 0.38f);
            Vector3 targetPosition = bodyBasePosition + Vector3.up * breathe;
            Quaternion headTarget = headBaseRotation;
            Quaternion leftArmTarget = leftArmBaseRotation;
            Quaternion rightArmTarget = rightArmBaseRotation;

            switch (Mode)
            {
                case AvatarMode.Listening:
                    targetPosition += new Vector3(0f, 0.025f, -0.045f);
                    headTarget *= Quaternion.Euler(5f, 0f, Mathf.Sin(t * 1.2f) * 1.5f);
                    break;
                case AvatarMode.Thinking:
                    headTarget *= Quaternion.Euler(-2f, -10f, -4f);
                    rightArmTarget *= Quaternion.Euler(-18f, 0f, -58f);
                    break;
                case AvatarMode.Speaking:
                    headTarget *= Quaternion.Euler(Mathf.Sin(t * 2.1f) * 2.5f, Mathf.Sin(t * 1.1f) * 3f, 0f);
                    leftArmTarget *= Quaternion.Euler(0f, 0f, Mathf.Sin(t * 2.4f) * 15f);
                    rightArmTarget *= Quaternion.Euler(0f, 0f, -Mathf.Sin(t * 2.0f + 0.6f) * 17f);
                    break;
                case AvatarMode.Success:
                    targetPosition += Vector3.up * Mathf.Abs(Mathf.Sin(stateTime * 5f)) * 0.09f * Mathf.Exp(-stateTime * 1.4f);
                    leftArmTarget *= Quaternion.Euler(0f, 0f, -145f);
                    rightArmTarget *= Quaternion.Euler(0f, 0f, 145f);
                    break;
                case AvatarMode.Error:
                    headTarget *= Quaternion.Euler(4f, 0f, Mathf.Sin(stateTime * 8f) * 2f * Mathf.Exp(-stateTime));
                    break;
            }

            body.localPosition = Vector3.Lerp(body.localPosition, targetPosition, Time.deltaTime * 5f);
            head.localPosition = Vector3.Lerp(head.localPosition, headBasePosition + Vector3.up * breathe * 0.4f, Time.deltaTime * 5f);
            head.localRotation = Quaternion.Slerp(head.localRotation, headTarget, Time.deltaTime * (3f + transition * 3f));
            leftArm.localRotation = Quaternion.Slerp(leftArm.localRotation, leftArmTarget, Time.deltaTime * 5f);
            rightArm.localRotation = Quaternion.Slerp(rightArm.localRotation, rightArmTarget, Time.deltaTime * 5f);
        }

        /// <summary>
        /// Restrained, realistic motion expressed in world-aligned character space so it does not
        /// depend on the imported bone axes. Negative pitch (x) lowers the chin toward the viewer.
        /// </summary>
        private void UpdateCharacterSpaceBody(float t)
        {
            float stateTime = t - stateChangedAt;
            float breathe = Mathf.Sin(t * 1.4f) * 0.004f;
            Vector3 targetPosition = bodyBasePosition + Vector3.up * breathe;
            Vector3 headEuler = new(Mathf.Sin(t * 0.7f) * 1.2f, Mathf.Sin(t * 0.45f) * 2f, 0f);
            Vector3 leftArmEuler = new(0f, 0f, Mathf.Sin(t * 1.4f) * 0.8f);
            Vector3 rightArmEuler = new(0f, 0f, -Mathf.Sin(t * 1.4f) * 0.8f);

            switch (Mode)
            {
                case AvatarMode.Listening:
                    targetPosition += new Vector3(0f, 0f, -0.012f);
                    headEuler += new Vector3(-4f, 0f, 3f);
                    break;
                case AvatarMode.Thinking:
                    headEuler += new Vector3(4f, -12f, -5f);
                    rightArmEuler += new Vector3(-20f, 0f, 8f);
                    break;
                case AvatarMode.Speaking:
                    headEuler += new Vector3(Mathf.Sin(t * 2.3f) * 2.4f, Mathf.Sin(t * 1.3f) * 3.5f, Mathf.Sin(t * 1.7f) * 1.2f);
                    leftArmEuler += new Vector3(Mathf.Sin(t * 2.1f) * 4f, 0f, Mathf.Sin(t * 1.6f) * 3f);
                    rightArmEuler += new Vector3(Mathf.Sin(t * 1.9f + 0.8f) * 4f, 0f, -Mathf.Sin(t * 1.5f + 0.4f) * 3f);
                    break;
                case AvatarMode.Success:
                    headEuler += new Vector3(-4f * Mathf.Exp(-stateTime * 2f) * Mathf.Sin(stateTime * 9f), 0f, 0f);
                    break;
                case AvatarMode.Error:
                    headEuler += new Vector3(-6f, 0f, Mathf.Sin(stateTime * 7f) * 3f * Mathf.Exp(-stateTime * 1.5f));
                    break;
            }

            body.localPosition = Vector3.Lerp(body.localPosition, targetPosition, Time.deltaTime * 4f);
            head.localRotation = Quaternion.Slerp(head.localRotation, CharacterSpaceRotation(head, headBaseRotation, headEuler), Time.deltaTime * 4f);
            leftArm.localRotation = Quaternion.Slerp(leftArm.localRotation, CharacterSpaceRotation(leftArm, leftArmBaseRotation, leftArmEuler), Time.deltaTime * 4f);
            rightArm.localRotation = Quaternion.Slerp(rightArm.localRotation, CharacterSpaceRotation(rightArm, rightArmBaseRotation, rightArmEuler), Time.deltaTime * 4f);
        }

        /// <summary>Local rotation that applies <paramref name="euler"/> (character space) on top of the base pose.</summary>
        private Quaternion CharacterSpaceRotation(Transform bone, Quaternion baseLocal, Vector3 euler)
        {
            Quaternion parent = bone.parent == null ? Quaternion.identity : bone.parent.rotation;
            Quaternion character = avatarRoot.rotation;
            Quaternion offset = character * Quaternion.Euler(euler) * Quaternion.Inverse(character);
            return Quaternion.Inverse(parent) * offset * parent * baseLocal;
        }

        private void UpdateFace(float t)
        {
            if (usingRiggedAsset)
            {
                UpdateRiggedFace(t);
                return;
            }

            float mouthOpen = 0.045f;
            float mouthWidth = 0.72f;
            float smile = 0f;
            float cheek = 0f;
            float browTilt = 0f;
            float browHeight = 0f;

            switch (Emotion)
            {
                case AvatarEmotion.Warm:
                    smile = 9f; mouthWidth = 0.86f; browHeight = 0.025f; cheek = 0.75f;
                    break;
                case AvatarEmotion.Curious:
                    browTilt = 9f; browHeight = 0.035f;
                    break;
                case AvatarEmotion.Excited:
                    smile = 13f; mouthWidth = 0.94f; browHeight = 0.065f; cheek = 1f;
                    break;
                case AvatarEmotion.Concerned:
                    smile = -7f; browTilt = -12f; browHeight = 0.025f;
                    break;
            }

            if (Mode == AvatarMode.Speaking)
            {
                mouthOpen = visemeMixer.Open;
                mouthWidth *= visemeMixer.Width;
            }
            else if (Mode == AvatarMode.Success)
            {
                mouthOpen = 0.16f;
                mouthWidth *= 1.08f;
            }
            else
            {
                ActiveViseme = "sil";
            }

            Vector3 mouthTargetScale = new(0.34f * mouthWidth, Mathf.Max(0.025f, mouthOpen), 0.075f);
            mouth.localScale = Vector3.Lerp(mouth.localScale, mouthTargetScale, Time.deltaTime * 17f);
            mouth.localRotation = Quaternion.Slerp(mouth.localRotation, Quaternion.identity, Time.deltaTime * 9f);

            float cornerY = -0.285f + Mathf.Abs(smile) * 0.0018f * Mathf.Sign(smile);
            leftMouthCorner.localPosition = Vector3.Lerp(leftMouthCorner.localPosition, new Vector3(-0.20f * mouthWidth, cornerY, -0.587f), Time.deltaTime * 9f);
            rightMouthCorner.localPosition = Vector3.Lerp(rightMouthCorner.localPosition, new Vector3(0.20f * mouthWidth, cornerY, -0.587f), Time.deltaTime * 9f);
            leftMouthCorner.localRotation = Quaternion.Slerp(leftMouthCorner.localRotation, Quaternion.Euler(0f, 0f, 90f - smile), Time.deltaTime * 9f);
            rightMouthCorner.localRotation = Quaternion.Slerp(rightMouthCorner.localRotation, Quaternion.Euler(0f, 0f, 90f + smile), Time.deltaTime * 9f);
            float cheekScale = Mathf.Lerp(0.72f, 1f, cheek);
            leftCheek.localScale = Vector3.Lerp(leftCheek.localScale, new Vector3(0.20f, 0.085f, 0.045f) * cheekScale, Time.deltaTime * 7f);
            rightCheek.localScale = Vector3.Lerp(rightCheek.localScale, new Vector3(0.20f, 0.085f, 0.045f) * cheekScale, Time.deltaTime * 7f);

            leftBrow.localPosition = Vector3.Lerp(leftBrow.localPosition, new Vector3(-0.29f, 0.44f + browHeight, -0.615f), Time.deltaTime * 7f);
            rightBrow.localPosition = Vector3.Lerp(rightBrow.localPosition, new Vector3(0.29f, 0.44f + browHeight, -0.615f), Time.deltaTime * 7f);
            leftBrow.localRotation = Quaternion.Slerp(leftBrow.localRotation, Quaternion.Euler(0f, 0f, 90f - browTilt), Time.deltaTime * 7f);
            rightBrow.localRotation = Quaternion.Slerp(rightBrow.localRotation, Quaternion.Euler(0f, 0f, 90f + browTilt), Time.deltaTime * 7f);

            float lidScale = Mathf.Lerp(0.002f, 0.29f, blinkAmount);
            float lidY = Mathf.Lerp(0.325f, 0.12f, blinkAmount);
            leftLid.localPosition = new Vector3(-0.30f, lidY, -0.605f);
            rightLid.localPosition = new Vector3(0.30f, lidY, -0.605f);
            leftLid.localScale = new Vector3(0.29f, lidScale, 0.15f);
            rightLid.localScale = new Vector3(0.29f, lidScale, 0.15f);
        }

        private void SampleSpeech(float now)
        {
            if (Mode != AvatarMode.Speaking)
            {
                visemeMixer.Clear();
                ActiveViseme = "sil";
                return;
            }

            if (!ttsTimelineActive || englishSpeechCues.Count == 0)
            {
                // Demo speech without a TTS utterance loops a planned English line.
                demoSpeechCues ??= EnglishVisemePlanner.Build(DemoSpeechLine, out demoSpeechDuration);
                visemeMixer.Evaluate(demoSpeechCues, Mathf.Repeat(now - speechStartedAt, demoSpeechDuration + 0.6f), -1f);
                ActiveViseme = visemeMixer.Dominant;
                return;
            }

            if (awaitingSpeechAudio)
            {
                visemeMixer.Clear();
                ActiveViseme = "sil";
                return;
            }

            speechTime = CurrentSpeechTime(now);
            visemeMixer.Evaluate(englishSpeechCues, speechTime, LoudnessAt(speechTime));
            ActiveViseme = visemeMixer.Dominant;
            if (Debug.isDebugBuild && now >= nextSpeechLogAt)
            {
                nextSpeechLogAt = now + 0.2f;
                Debug.Log($"TTS_CLOCK: t={speechTime:F3}, audioClock={audioClockActive}, viseme={ActiveViseme}, jaw={visemeMixer.Jaw:F2}, loud={LoudnessAt(speechTime):F2}");
            }
        }

        private float CurrentSpeechTime(float now)
        {
            if (!audioClockActive) return Mathf.Max(0f, now - speechStartedAt);

            float predicted = speechTime + Time.deltaTime;
            long positionMs = AndroidTextToSpeech.GetPlaybackPositionMs();
            if (positionMs < 0) return predicted;
            float position = positionMs / 1000f;
            // Follow the audio presentation clock, smoothing the coarse position updates.
            return Mathf.Abs(position - predicted) > 0.08f ? position : Mathf.Lerp(predicted, position, 0.25f);
        }

        private float LoudnessAt(float time)
        {
            if (speechEnvelope == null || speechEnvelope.Length == 0 || speechEnvelopeRate <= 0f) return -1f;
            float sample = time * speechEnvelopeRate;
            int index = Mathf.Clamp(Mathf.FloorToInt(sample), 0, speechEnvelope.Length - 1);
            int next = Mathf.Min(index + 1, speechEnvelope.Length - 1);
            return Mathf.Lerp(speechEnvelope[index], speechEnvelope[next], sample - Mathf.Floor(sample)) / 100f;
        }

        private void CaptureRiggedDefaults()
        {
            leftEyeBaseInHead = head.InverseTransformPoint(leftPupil.position);
            rightEyeBaseInHead = head.InverseTransformPoint(rightPupil.position);
        }

        private void UpdateRiggedFace(float t)
        {
            float smile = 0f, frown = 0f, browUpLeft = 0f, browUpRight = 0f, browDown = 0f, browInner = 0f, cheek = 0f;
            switch (Emotion)
            {
                case AvatarEmotion.Warm:
                    smile = 0.45f; browUpLeft = browUpRight = 0.15f; cheek = 0.25f;
                    break;
                case AvatarEmotion.Curious:
                    browUpLeft = 0.85f; browUpRight = 0.20f; browInner = 0.25f;
                    break;
                case AvatarEmotion.Excited:
                    smile = 0.85f; browUpLeft = browUpRight = 0.55f; cheek = 0.6f;
                    break;
                case AvatarEmotion.Concerned:
                    frown = 0.65f; browInner = 0.9f; browDown = 0.15f;
                    break;
            }

            float successOpen = 0f;
            switch (Mode)
            {
                case AvatarMode.Listening:
                    browUpLeft += 0.2f; browUpRight += 0.2f;
                    break;
                case AvatarMode.Thinking:
                    browDown = Mathf.Max(browDown, 0.35f); browUpLeft = 0.6f;
                    break;
                case AvatarMode.Speaking:
                    // Expressions stay layered under speech without overpowering the visemes.
                    smile = Mathf.Min(smile, 0.4f);
                    frown = Mathf.Min(frown, 0.4f);
                    break;
                case AvatarMode.Success:
                    smile = 1f; successOpen = 0.45f; cheek = 0.8f;
                    break;
            }

            float intensity = Mathf.Lerp(0.75f, 1f, Intensity);
            SetFaceShape("mouthSmile.L", smile * intensity, 10f);
            SetFaceShape("mouthSmile.R", smile * intensity, 10f);
            SetFaceShape("mouthFrown.L", frown * intensity, 10f);
            SetFaceShape("mouthFrown.R", frown * intensity, 10f);
            SetFaceShape("browOuterUp.L", Mathf.Clamp01(browUpLeft), 8f);
            SetFaceShape("browOuterUp.R", Mathf.Clamp01(browUpRight), 8f);
            SetFaceShape("browDown.L", browDown, 8f);
            SetFaceShape("browDown.R", browDown, 8f);
            SetFaceShape("browInnerUp", browInner, 8f);
            SetFaceShape("cheekPuff", cheek, 6f);
            SetFaceShape("eyeBlink.L", blinkAmount, 0f);
            SetFaceShape("eyeBlink.R", blinkAmount, 0f);

            // The mixer already blends and times speech, so visemes are applied directly.
            bool speaking = Mode == AvatarMode.Speaking;
            float speechRate = speaking ? 0f : 16f;
            float speechGain = speaking ? Mathf.Lerp(0.85f, 1f, Intensity) : 0f;
            for (int i = 1; i < visemeShapeIndices.Length; i++)
            {
                float target = visemeMixer.Weights[i] * speechGain;
                if (Mode == AvatarMode.Success && VisemeMixer.Visemes[i] == "aa") target = successOpen;
                SetFaceShape(visemeShapeIndices[i], target, speechRate);
            }
            float jawScale = ActiveProfile == null ? 0.55f : ActiveProfile.JawOpenScale;
            SetFaceShape("jawOpen", speaking ? visemeMixer.Jaw * jawScale : successOpen * 0.3f, speechRate);
        }

        private void SetFaceShape(string shapeName, float weight, float rate)
        {
            if (faceShapeIndices.TryGetValue(shapeName, out int index)) SetFaceShape(index, weight, rate);
        }

        /// <param name="rate">Smoothing speed; zero applies the weight immediately.</param>
        private void SetFaceShape(int index, float weight, float rate)
        {
            if (riggedFace == null || index < 0) return;
            float target = Mathf.Clamp01(weight) * 100f;
            float value = rate <= 0f ? target : Mathf.Lerp(faceShapeWeights[index], target, Mathf.Clamp01(Time.deltaTime * rate));
            if (Mathf.Abs(value - faceShapeWeights[index]) < 0.01f) return;
            faceShapeWeights[index] = value;
            riggedFace.SetBlendShapeWeight(index, value);
        }

        private void FinishEnglishSpeech(string reason)
        {
            ttsTimelineActive = false;
            audioTimelineReceived = false;
            audioClockActive = false;
            speechEnvelope = null;
            activeUtteranceId = null;
            englishSpeechCues.Clear();
            ActiveSpeechText = string.Empty;
            ActiveViseme = "sil";
            ApplyCommand(AvatarMode.Idle, AvatarEmotion.Warm, 0.45f);
            Debug.Log($"TTS_FINISHED: {reason}");
            AndroidHost.NotifySpeechFinished(LastAssistantResponseId, reason);
        }

        private void OnApplicationQuit()
        {
            AndroidTextToSpeech.Shutdown();
        }

        private void UpdateBlink(float t)
        {
            float target = 0f;
            if (t >= nextBlinkAt && Mathf.Approximately(blinkVelocity, 0f))
            {
                blinkVelocity = 1f;
            }

            if (blinkVelocity > 0f)
            {
                target = 1f;
                if (blinkAmount > 0.94f) blinkVelocity = -1f;
            }
            else if (blinkVelocity < 0f && blinkAmount < 0.04f)
            {
                blinkVelocity = 0f;
                blinkAmount = 0f;
                nextBlinkAt = t + UnityEngine.Random.Range(2.1f, 4.6f);
            }

            blinkAmount = Mathf.MoveTowards(blinkAmount, target, Time.deltaTime * (blinkVelocity > 0f ? 10f : 7f));
        }

        private void UpdateGaze(float t)
        {
            if (t >= nextGazeAt)
            {
                float range = Mode == AvatarMode.Listening ? 0.045f : 0.09f;
                gazeTarget = UnityEngine.Random.insideUnitCircle * range;
                if (Mode == AvatarMode.Thinking) gazeTarget += new Vector2(0.08f, 0.045f);
                nextGazeAt = t + UnityEngine.Random.Range(0.8f, 2.2f);
            }

            gaze = Vector2.Lerp(gaze, gazeTarget, Time.deltaTime * 5f);
            if (usingRiggedAsset)
            {
                Vector3 offset = (avatarRoot.right * gaze.x + avatarRoot.up * gaze.y) * ActiveProfile.GazeScale;
                leftPupil.position = head.TransformPoint(leftEyeBaseInHead) + offset;
                rightPupil.position = head.TransformPoint(rightEyeBaseInHead) + offset;
            }
            else
            {
                leftPupil.localPosition = new Vector3(gaze.x, gaze.y, -0.53f);
                rightPupil.localPosition = new Vector3(gaze.x, gaze.y, -0.53f);
            }
        }

        private void UpdateStatusOrb(float t)
        {
            Color target = Mode switch
            {
                AvatarMode.Listening => new Color(0.20f, 0.85f, 0.95f),
                AvatarMode.Thinking => new Color(0.66f, 0.42f, 1.00f),
                AvatarMode.Speaking => new Color(0.16f, 0.92f, 0.70f),
                AvatarMode.Success => new Color(0.30f, 0.95f, 0.46f),
                AvatarMode.Error => new Color(1.00f, 0.35f, 0.34f),
                _ => new Color(0.22f, 0.65f, 0.72f)
            };

            statusColor = Color.Lerp(statusColor, target, Time.deltaTime * 6f);
            Renderer renderer = statusOrb.GetComponent<Renderer>();
            MaterialPropertyBlock block = new();
            renderer.GetPropertyBlock(block);
            block.SetColor("_BaseColor", statusColor);
            block.SetColor("_Color", statusColor);
            block.SetColor("_EmissionColor", statusColor * 1.6f);
            renderer.SetPropertyBlock(block);
            float pulse = 1f + Mathf.Sin(t * (Mode == AvatarMode.Thinking ? 4.5f : 2.2f)) * 0.08f;
            statusOrb.localScale = statusOrbBaseScale * pulse;
        }

        private void BuildAvatar(AvatarCharacter preferred)
        {
            avatarRoot = new GameObject("GeneratedAvatar").transform;
            avatarRoot.SetParent(transform, false);

            Create(PrimitiveType.Cylinder, "GroundShadow", avatarRoot, new Vector3(0f, 0.045f, 0.08f), new Vector3(0.82f, 0.018f, 0.47f), "shadow");
            body = new GameObject("BodyMotion").transform;
            body.SetParent(avatarRoot, false);
            AvatarCharacter other = preferred == AvatarCharacter.CoolMan ? AvatarCharacter.Milo : AvatarCharacter.CoolMan;
            if (TryBuildRiggedAvatar(AvatarCharacterProfile.For(preferred)) || TryBuildRiggedAvatar(AvatarCharacterProfile.For(other))) return;

            Create(PrimitiveType.Capsule, "LeftLeg", body, new Vector3(-0.23f, 0.46f, 0f), new Vector3(0.22f, 0.23f, 0.24f), "skin");
            Create(PrimitiveType.Capsule, "RightLeg", body, new Vector3(0.23f, 0.46f, 0f), new Vector3(0.22f, 0.23f, 0.24f), "skin");
            Create(PrimitiveType.Sphere, "LeftSock", body, new Vector3(-0.23f, 0.25f, -0.02f), new Vector3(0.25f, 0.13f, 0.27f), "white");
            Create(PrimitiveType.Sphere, "RightSock", body, new Vector3(0.23f, 0.25f, -0.02f), new Vector3(0.25f, 0.13f, 0.27f), "white");
            Create(PrimitiveType.Sphere, "LeftShoe", body, new Vector3(-0.24f, 0.14f, -0.16f), new Vector3(0.36f, 0.14f, 0.50f), "shoeYellow");
            Create(PrimitiveType.Sphere, "RightShoe", body, new Vector3(0.24f, 0.14f, -0.16f), new Vector3(0.36f, 0.14f, 0.50f), "shoeYellow");
            Create(PrimitiveType.Sphere, "Shorts", body, new Vector3(0f, 1.00f, 0.01f), new Vector3(0.76f, 0.43f, 0.47f), "shortsBlue");
            Create(PrimitiveType.Sphere, "Sweatshirt", body, new Vector3(0f, 1.54f, -0.03f), new Vector3(0.72f, 0.58f, 0.46f), "shirtRed");

            leftArm = BuildArm("Left", body, new Vector3(-0.66f, 1.67f, 0f), 8f);
            rightArm = BuildArm("Right", body, new Vector3(0.66f, 1.67f, 0f), -8f);

            head = new GameObject("HeadMotion").transform;
            head.SetParent(body, false);
            head.localPosition = new Vector3(0f, 2.53f, 0f);
            Create(PrimitiveType.Sphere, "Head", head, Vector3.zero, new Vector3(1.25f, 1.06f, 0.98f), "skin");
            Create(PrimitiveType.Sphere, "LeftEar", head, new Vector3(-0.66f, -0.01f, -0.01f), new Vector3(0.29f, 0.34f, 0.22f), "skin");
            Create(PrimitiveType.Sphere, "RightEar", head, new Vector3(0.66f, -0.01f, -0.01f), new Vector3(0.29f, 0.34f, 0.22f), "skin");
            Create(PrimitiveType.Sphere, "LeftEarInner", head, new Vector3(-0.635f, -0.015f, -0.125f), new Vector3(0.12f, 0.18f, 0.055f), "skinRoseSoft");
            Create(PrimitiveType.Sphere, "RightEarInner", head, new Vector3(0.635f, -0.015f, -0.125f), new Vector3(0.12f, 0.18f, 0.055f), "skinRoseSoft");
            Create(PrimitiveType.Sphere, "Nose", head, new Vector3(0f, -0.035f, -0.555f), new Vector3(0.085f, 0.070f, 0.065f), "skinRose");

            leftEye = Create(PrimitiveType.Sphere, "LeftEye", head, new Vector3(-0.30f, 0.12f, -0.52f), new Vector3(0.28f, 0.33f, 0.16f), "white");
            rightEye = Create(PrimitiveType.Sphere, "RightEye", head, new Vector3(0.30f, 0.12f, -0.52f), new Vector3(0.28f, 0.33f, 0.16f), "white");
            leftPupil = Create(PrimitiveType.Sphere, "LeftIris", leftEye, new Vector3(0f, 0f, -0.53f), new Vector3(0.40f, 0.37f, 0.20f), "iris");
            rightPupil = Create(PrimitiveType.Sphere, "RightIris", rightEye, new Vector3(0f, 0f, -0.53f), new Vector3(0.40f, 0.37f, 0.20f), "iris");
            Transform leftBlackPupil = Create(PrimitiveType.Sphere, "LeftPupil", leftPupil, new Vector3(0f, -0.02f, -0.53f), new Vector3(0.54f, 0.57f, 0.24f), "pupil");
            Transform rightBlackPupil = Create(PrimitiveType.Sphere, "RightPupil", rightPupil, new Vector3(0f, -0.02f, -0.53f), new Vector3(0.54f, 0.57f, 0.24f), "pupil");
            Create(PrimitiveType.Sphere, "LeftEyeGlint", leftBlackPupil, new Vector3(-0.17f, 0.20f, -0.55f), new Vector3(0.26f, 0.26f, 0.22f), "white");
            Create(PrimitiveType.Sphere, "RightEyeGlint", rightBlackPupil, new Vector3(-0.17f, 0.20f, -0.55f), new Vector3(0.26f, 0.26f, 0.22f), "white");
            leftLid = Create(PrimitiveType.Sphere, "LeftLid", head, new Vector3(-0.30f, 0.325f, -0.605f), new Vector3(0.29f, 0.002f, 0.15f), "skin");
            rightLid = Create(PrimitiveType.Sphere, "RightLid", head, new Vector3(0.30f, 0.325f, -0.605f), new Vector3(0.29f, 0.002f, 0.15f), "skin");
            leftBrow = Create(PrimitiveType.Capsule, "LeftBrow", head, new Vector3(-0.29f, 0.44f, -0.615f), new Vector3(0.085f, 0.24f, 0.070f), "hair");
            leftBrow.localRotation = Quaternion.Euler(0f, 0f, 90f);
            rightBrow = Create(PrimitiveType.Capsule, "RightBrow", head, new Vector3(0.29f, 0.44f, -0.615f), new Vector3(0.085f, 0.24f, 0.070f), "hair");
            rightBrow.localRotation = Quaternion.Euler(0f, 0f, 90f);
            leftCheek = Create(PrimitiveType.Sphere, "LeftCheek", head, new Vector3(-0.43f, -0.17f, -0.515f), new Vector3(0.16f, 0.065f, 0.04f), "blush");
            rightCheek = Create(PrimitiveType.Sphere, "RightCheek", head, new Vector3(0.43f, -0.17f, -0.515f), new Vector3(0.16f, 0.065f, 0.04f), "blush");
            mouth = Create(PrimitiveType.Sphere, "MouthInterior", head, new Vector3(0f, -0.30f, -0.55f), new Vector3(0.25f, 0.025f, 0.075f), "mouth");
            leftMouthCorner = Create(PrimitiveType.Capsule, "LeftMouthCorner", head, new Vector3(-0.18f, -0.285f, -0.587f), new Vector3(0.025f, 0.10f, 0.025f), "mouthLine");
            leftMouthCorner.localRotation = Quaternion.Euler(0f, 0f, 81f);
            rightMouthCorner = Create(PrimitiveType.Capsule, "RightMouthCorner", head, new Vector3(0.18f, -0.285f, -0.587f), new Vector3(0.025f, 0.10f, 0.025f), "mouthLine");
            rightMouthCorner.localRotation = Quaternion.Euler(0f, 0f, 99f);

            BuildHair();
            BuildStatusPin();
        }

        private bool TryBuildRiggedAvatar(AvatarCharacterProfile profile)
        {
            GameObject prefab = Resources.Load<GameObject>(profile.ResourcePath);
            if (prefab == null) return false;

            GameObject instance = Instantiate(prefab, body);
            instance.name = $"{profile.Character}Character";
            instance.transform.localPosition = Vector3.zero;
            instance.transform.localRotation = Quaternion.Euler(0f, 180f, 0f);

            head = FindDescendant(instance.transform, "Head");
            leftPupil = FindDescendant(instance.transform, profile.LeftEyeBone);
            rightPupil = FindDescendant(instance.transform, profile.RightEyeBone);
            leftArm = FindDescendant(instance.transform, profile.ScreenLeftArm);
            rightArm = FindDescendant(instance.transform, profile.ScreenRightArm);
            statusOrb = FindDescendant(instance.transform, "Status_Orb");
            Transform face = FindDescendant(instance.transform, profile.FaceRenderer);
            riggedFace = face == null ? null : face.GetComponent<SkinnedMeshRenderer>();

            Transform[] required = { head, leftPupil, rightPupil, leftArm, rightArm, statusOrb };
            bool complete = riggedFace != null && riggedFace.sharedMesh != null && Array.TrueForAll(required, item => item != null);
            if (complete) BindFaceShapes();
            if (!complete || visemeShapeIndices[VisemeMixer.IndexOf("PP")] < 0)
            {
                Debug.LogWarning($"Rigged avatar {profile.Character} is incomplete; trying the next fallback.");
                riggedFace = null;
                if (Application.isPlaying) Destroy(instance);
                else DestroyImmediate(instance);
                return false;
            }

            ActiveProfile = profile;
            characterRoot = instance.transform;
            gestureAnimation = instance.GetComponent<UnityEngine.Animation>();
            if (gestureAnimation != null)
            {
                gestureAnimation.playAutomatically = false;
                gestureAnimation.Stop();
                // A frame of the handshake clip gives a natural arms-down idle pose.
                AnimationClip idle = FindGestureClip(profile.IdlePoseClip);
                if (idle != null) idle.SampleAnimation(instance, 0f);
            }

            if (profile.UseClayShader) ApplyRiggedMaterials(instance);
            else ConfigureTexturedRenderers(instance, profile);
            usingRiggedAsset = true;
            Debug.Log($"RIGGED_AVATAR_ACTIVE: profile={profile.Character}, renderers={instance.GetComponentsInChildren<Renderer>(true).Length}, skinned={instance.GetComponentsInChildren<SkinnedMeshRenderer>(true).Length}, faceBlendShapes={riggedFace.sharedMesh.blendShapeCount}, clips={(gestureAnimation == null ? 0 : gestureAnimation.GetClipCount())}");
            return true;
        }

        private static void ConfigureTexturedRenderers(GameObject instance, AvatarCharacterProfile profile)
        {
            foreach (Renderer renderer in instance.GetComponentsInChildren<Renderer>(true))
            {
                bool orb = renderer.name == "Status_Orb";
                renderer.shadowCastingMode = profile.CastShadows && !orb ? ShadowCastingMode.On : ShadowCastingMode.Off;
                renderer.receiveShadows = profile.CastShadows;
                if (renderer is SkinnedMeshRenderer skinned) skinned.updateWhenOffscreen = true;
            }
        }

        private void BindFaceShapes()
        {
            Mesh mesh = riggedFace.sharedMesh;
            faceShapeIndices.Clear();
            for (int i = 0; i < mesh.blendShapeCount; i++) faceShapeIndices[mesh.GetBlendShapeName(i)] = i;
            for (int i = 0; i < visemeShapeIndices.Length; i++)
                visemeShapeIndices[i] = faceShapeIndices.TryGetValue($"viseme_{VisemeMixer.Visemes[i]}", out int index) ? index : -1;
            faceShapeWeights = new float[mesh.blendShapeCount];
        }

        private void ApplyRiggedMaterials(GameObject instance)
        {
            Material clay = GetClayMaterial();
            foreach (Renderer renderer in instance.GetComponentsInChildren<Renderer>(true))
            {
                Material[] shared = new Material[renderer.sharedMaterials.Length];
                for (int i = 0; i < shared.Length; i++) shared[i] = clay;
                renderer.sharedMaterials = shared;
                renderer.shadowCastingMode = ShadowCastingMode.Off;
                renderer.receiveShadows = false;
                if (renderer is SkinnedMeshRenderer skinned) skinned.updateWhenOffscreen = true;
            }
        }

        private Material GetClayMaterial()
        {
            if (materials.TryGetValue("clay", out Material existing)) return existing;
            Shader shader = Shader.Find("PersonalAssistant/MiloClayToon");
            if (shader == null)
            {
                Debug.LogWarning("MiloClayToon shader is unavailable; using URP Lit.");
                return GetMaterial("skin");
            }
            Material material = new(shader) { name = "Runtime_MiloClay" };
            materials["clay"] = material;
            return material;
        }

        private static Transform FindDescendant(Transform root, string objectName)
        {
            foreach (Transform item in root.GetComponentsInChildren<Transform>(true))
                if (item.name == objectName) return item;
            return null;
        }

        private Transform BuildArm(string side, Transform parent, Vector3 position, float zRotation)
        {
            Transform root = new GameObject($"{side}ArmMotion").transform;
            root.SetParent(parent, false);
            root.localPosition = position;
            root.localRotation = Quaternion.Euler(0f, 0f, zRotation);
            Create(PrimitiveType.Capsule, $"{side}Sleeve", root, new Vector3(0f, 0.02f, 0f), new Vector3(0.23f, 0.34f, 0.25f), "shirtRed");
            Create(PrimitiveType.Sphere, $"{side}Cuff", root, new Vector3(0f, -0.27f, -0.01f), new Vector3(0.24f, 0.12f, 0.24f), "shirtRedDark");
            Create(PrimitiveType.Sphere, $"{side}Hand", root, new Vector3(0f, -0.41f, -0.02f), new Vector3(0.23f, 0.24f, 0.22f), "skin");
            return root;
        }

        private void BuildStatusPin()
        {
            Vector3 center = new(0.35f, 1.64f, -0.49f);
            Create(PrimitiveType.Sphere, "PinPetalTop", body, center + new Vector3(0f, 0.060f, 0f), new Vector3(0.060f, 0.082f, 0.030f), "white");
            Create(PrimitiveType.Sphere, "PinPetalLeft", body, center + new Vector3(-0.055f, -0.020f, 0f), new Vector3(0.082f, 0.060f, 0.030f), "white");
            Create(PrimitiveType.Sphere, "PinPetalRight", body, center + new Vector3(0.055f, -0.020f, 0f), new Vector3(0.082f, 0.060f, 0.030f), "white");
            statusOrb = Create(PrimitiveType.Sphere, "StateIndicator", body, center + new Vector3(0f, 0f, -0.025f), Vector3.one * 0.062f, "status");
        }

        private void BuildHair()
        {
            Create(PrimitiveType.Sphere, "HairCap", head, new Vector3(0f, 0.46f, 0.08f), new Vector3(1.18f, 0.56f, 0.95f), "hair");
            Transform signatureLock = Create(PrimitiveType.Capsule, "SignatureForeheadLock", head, new Vector3(0.25f, 0.64f, -0.49f), new Vector3(0.085f, 0.19f, 0.075f), "hair");
            signatureLock.localRotation = Quaternion.Euler(0f, 0f, 48f);
        }

        private Transform Create(PrimitiveType type, string objectName, Transform parent, Vector3 position, Vector3 scale, string materialKey)
        {
            GameObject item = GameObject.CreatePrimitive(type);
            item.name = objectName;
            item.transform.SetParent(parent, false);
            item.transform.localPosition = position;
            item.transform.localScale = scale;
            Collider collider = item.GetComponent<Collider>();
            if (collider != null)
            {
                if (Application.isPlaying) Destroy(collider);
                else DestroyImmediate(collider);
            }
            Renderer renderer = item.GetComponent<Renderer>();
            renderer.shadowCastingMode = ShadowCastingMode.Off;
            renderer.receiveShadows = false;
            Material material = GetMaterial(materialKey);
            renderer.sharedMaterial = material;
            Color color = material.HasProperty("_BaseColor") ? material.GetColor("_BaseColor") : material.color;
            MaterialPropertyBlock block = new();
            block.SetColor("_BaseColor", color);
            block.SetColor("_Color", color);
            renderer.SetPropertyBlock(block);
            return item.transform;
        }

        private Material GetMaterial(string key)
        {
            if (materials.TryGetValue(key, out Material existing)) return existing;
            Shader shader = Shader.Find("Universal Render Pipeline/Lit") ?? Shader.Find("Standard");
            Material material = new(shader) { name = $"Runtime_{key}" };
            Color color = key switch
            {
                "skin" => new Color(1.00f, 0.70f, 0.59f),
                "skinRose" => new Color(1.00f, 0.57f, 0.49f),
                "skinRoseSoft" => new Color(0.95f, 0.49f, 0.43f),
                "blush" => new Color(0.98f, 0.55f, 0.55f),
                "hair" => new Color(0.075f, 0.055f, 0.055f),
                "shirtRed" => new Color(0.82f, 0.055f, 0.065f),
                "shirtRedDark" => new Color(0.62f, 0.028f, 0.040f),
                "shortsBlue" => new Color(0.20f, 0.16f, 0.56f),
                "shoeYellow" => new Color(0.96f, 0.66f, 0.06f),
                "shoeSole" => new Color(0.25f, 0.14f, 0.035f),
                "teal" => new Color(0.04f, 0.38f, 0.43f),
                "tealLight" => new Color(0.08f, 0.54f, 0.58f),
                "cream" => new Color(0.96f, 0.91f, 0.82f),
                "charcoal" => new Color(0.10f, 0.11f, 0.13f),
                "silver" => new Color(0.66f, 0.72f, 0.72f),
                "white" => new Color(0.98f, 0.98f, 0.96f),
                "iris" => new Color(0.26f, 0.10f, 0.035f),
                "pupil" => new Color(0.035f, 0.018f, 0.014f),
                "mouth" => new Color(0.25f, 0.025f, 0.025f),
                "tongue" => new Color(0.95f, 0.18f, 0.25f),
                "mouthLine" => new Color(0.16f, 0.018f, 0.018f),
                "shadow" => new Color(0.80f, 0.68f, 0.60f),
                _ => new Color(0.20f, 0.80f, 0.75f)
            };
            material.color = color;
            if (material.HasProperty("_BaseColor")) material.SetColor("_BaseColor", color);
            material.SetFloat("_Smoothness", key is "hair" or "iris" or "pupil" or "shoeYellow" ? 0.48f : 0.22f);
            materials[key] = material;
            return material;
        }

        private static float Smooth01(float value)
        {
            value = Mathf.Clamp01(value);
            return value * value * (3f - 2f * value);
        }

        [Serializable]
        private sealed class TtsRangeEvent
        {
            public int start;
            public int end;
            public int frame;
        }

        [Serializable]
        private sealed class TtsTimelineEvent
        {
            public string utteranceId;
            public int sampleRate;
            public int durationMs;
            public float envelopeRate;
            public TtsRangeEvent[] words;
            public int[] envelope;
        }
    }
}
