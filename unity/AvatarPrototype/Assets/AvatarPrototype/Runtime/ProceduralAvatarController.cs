using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;

namespace PersonalAssistant.Avatar
{
    /// <summary>
    /// A deliberately asset-free avatar stand-in. It validates the runtime contract,
    /// animation layering, state transitions, gaze, blinking, and deterministic visemes
    /// before the final rigged character replaces the generated geometry.
    /// </summary>
    public sealed class ProceduralAvatarController : MonoBehaviour
    {
        public AvatarMode Mode { get; private set; } = AvatarMode.Idle;
        public AvatarEmotion Emotion { get; private set; } = AvatarEmotion.Neutral;
        public float Intensity { get; private set; } = 0.5f;
        public string ActiveViseme { get; private set; } = "sil";

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

        private Vector3 bodyBasePosition;
        private Vector3 headBasePosition;
        private Quaternion headBaseRotation;
        private Quaternion leftArmBaseRotation;
        private Quaternion rightArmBaseRotation;
        private float blinkAmount;
        private float blinkVelocity;
        private float nextBlinkAt;
        private float stateChangedAt;
        private float speechStartedAt;
        private Color statusColor = new(0.20f, 0.80f, 0.75f);
        private Vector2 gaze;
        private Vector2 gazeTarget;
        private float nextGazeAt;

        private static readonly VisemeCue[] DemoVisemes =
        {
            new(0.00f, "sil", 0.04f, 0.70f), new(0.18f, "HH", 0.24f, 0.92f),
            new(0.35f, "E", 0.16f, 1.10f), new(0.52f, "LL", 0.20f, 0.72f),
            new(0.70f, "oh", 0.26f, 0.82f), new(0.92f, "PP", 0.04f, 0.62f),
            new(1.08f, "aa", 0.35f, 0.92f), new(1.32f, "SS", 0.10f, 1.18f),
            new(1.55f, "ih", 0.16f, 0.86f), new(1.78f, "SS", 0.08f, 1.15f),
            new(1.98f, "TH", 0.12f, 0.72f), new(2.14f, "aa", 0.34f, 0.96f),
            new(2.38f, "nn", 0.10f, 0.72f), new(2.58f, "kk", 0.16f, 0.78f),
            new(2.80f, "aa", 0.30f, 0.90f), new(3.04f, "nn", 0.08f, 0.74f),
            new(3.24f, "PP", 0.03f, 0.62f), new(3.42f, "ou", 0.20f, 0.62f),
            new(3.68f, "RR", 0.15f, 0.76f), new(3.90f, "sil", 0.03f, 0.72f)
        };

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
            BuildAvatar();
            bodyBasePosition = body.localPosition;
            headBasePosition = head.localPosition;
            headBaseRotation = head.localRotation;
            leftArmBaseRotation = leftArm.localRotation;
            rightArmBaseRotation = rightArm.localRotation;
        }

        private void Update()
        {
            float t = Time.time;
            UpdateBlink(t);
            UpdateGaze(t);
            UpdateBody(t);
            UpdateFace(t);
            UpdateStatusOrb(t);
        }

        public void ApplyCommand(AvatarMode mode, AvatarEmotion emotion, float intensity = 0.5f)
        {
            if (Mode != mode)
            {
                stateChangedAt = Time.time;
                if (mode == AvatarMode.Speaking)
                {
                    speechStartedAt = Time.time;
                }
            }

            Mode = mode;
            Emotion = emotion;
            Intensity = Mathf.Clamp01(intensity);
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

        private void UpdateBody(float t)
        {
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
                    rightArmTarget *= Quaternion.Euler(-24f, 0f, -20f);
                    break;
                case AvatarMode.Speaking:
                    headTarget *= Quaternion.Euler(Mathf.Sin(t * 2.1f) * 2.5f, Mathf.Sin(t * 1.1f) * 3f, 0f);
                    leftArmTarget *= Quaternion.Euler(0f, 0f, Mathf.Sin(t * 2.4f) * 8f);
                    rightArmTarget *= Quaternion.Euler(0f, 0f, -Mathf.Sin(t * 2.0f + 0.6f) * 9f);
                    break;
                case AvatarMode.Success:
                    targetPosition += Vector3.up * Mathf.Abs(Mathf.Sin(stateTime * 5f)) * 0.09f * Mathf.Exp(-stateTime * 1.4f);
                    leftArmTarget *= Quaternion.Euler(0f, 0f, -28f);
                    rightArmTarget *= Quaternion.Euler(0f, 0f, 28f);
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

        private void UpdateFace(float t)
        {
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
                EvaluateViseme(Mathf.Repeat(t - speechStartedAt, 4.12f), out mouthOpen, out float visemeWidth);
                mouthWidth *= visemeWidth;
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

            leftBrow.localPosition = Vector3.Lerp(leftBrow.localPosition, new Vector3(-0.30f, 0.405f + browHeight, -0.585f), Time.deltaTime * 7f);
            rightBrow.localPosition = Vector3.Lerp(rightBrow.localPosition, new Vector3(0.30f, 0.405f + browHeight, -0.585f), Time.deltaTime * 7f);
            leftBrow.localRotation = Quaternion.Slerp(leftBrow.localRotation, Quaternion.Euler(0f, 0f, 90f - browTilt), Time.deltaTime * 7f);
            rightBrow.localRotation = Quaternion.Slerp(rightBrow.localRotation, Quaternion.Euler(0f, 0f, 90f + browTilt), Time.deltaTime * 7f);

            float lidScale = Mathf.Lerp(0.002f, 0.39f, blinkAmount);
            float lidY = Mathf.Lerp(0.345f, 0.12f, blinkAmount);
            leftLid.localPosition = new Vector3(-0.30f, lidY, -0.605f);
            rightLid.localPosition = new Vector3(0.30f, lidY, -0.605f);
            leftLid.localScale = new Vector3(0.39f, lidScale, 0.17f);
            rightLid.localScale = new Vector3(0.39f, lidScale, 0.17f);
        }

        private void EvaluateViseme(float time, out float open, out float width)
        {
            VisemeCue current = DemoVisemes[0];
            for (int i = 1; i < DemoVisemes.Length; i++)
            {
                if (DemoVisemes[i].Time > time) break;
                current = DemoVisemes[i];
            }

            ActiveViseme = current.Name;
            open = current.Open;
            width = current.Width;
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
            leftPupil.localPosition = new Vector3(gaze.x, gaze.y, -0.53f);
            rightPupil.localPosition = new Vector3(gaze.x, gaze.y, -0.53f);
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
            renderer.SetPropertyBlock(block);
            float pulse = 1f + Mathf.Sin(t * (Mode == AvatarMode.Thinking ? 4.5f : 2.2f)) * 0.08f;
            statusOrb.localScale = Vector3.one * 0.075f * pulse;
        }

        private void BuildAvatar()
        {
            avatarRoot = new GameObject("GeneratedAvatar").transform;
            avatarRoot.SetParent(transform, false);

            Transform backdrop = Create(PrimitiveType.Cylinder, "BackdropHalo", avatarRoot, new Vector3(0f, 2.20f, 1.05f), new Vector3(2.15f, 0.025f, 3.05f), "backdrop");
            backdrop.localRotation = Quaternion.Euler(90f, 0f, 0f);
            Create(PrimitiveType.Cylinder, "GroundShadow", avatarRoot, new Vector3(0f, 0.045f, 0.08f), new Vector3(0.82f, 0.018f, 0.47f), "shadow");
            body = new GameObject("BodyMotion").transform;
            body.SetParent(avatarRoot, false);

            Create(PrimitiveType.Capsule, "LeftLeg", body, new Vector3(-0.23f, 0.66f, 0f), new Vector3(0.23f, 0.50f, 0.25f), "skin");
            Create(PrimitiveType.Capsule, "RightLeg", body, new Vector3(0.23f, 0.66f, 0f), new Vector3(0.23f, 0.50f, 0.25f), "skin");
            Create(PrimitiveType.Sphere, "LeftSock", body, new Vector3(-0.23f, 0.28f, -0.02f), new Vector3(0.28f, 0.19f, 0.30f), "white");
            Create(PrimitiveType.Sphere, "RightSock", body, new Vector3(0.23f, 0.28f, -0.02f), new Vector3(0.28f, 0.19f, 0.30f), "white");
            Create(PrimitiveType.Sphere, "LeftShoe", body, new Vector3(-0.24f, 0.17f, -0.14f), new Vector3(0.36f, 0.17f, 0.52f), "white");
            Create(PrimitiveType.Sphere, "RightShoe", body, new Vector3(0.24f, 0.17f, -0.14f), new Vector3(0.36f, 0.17f, 0.52f), "white");
            Create(PrimitiveType.Sphere, "LeftShoeAccent", body, new Vector3(-0.24f, 0.15f, -0.39f), new Vector3(0.29f, 0.095f, 0.16f), "tealLight");
            Create(PrimitiveType.Sphere, "RightShoeAccent", body, new Vector3(0.24f, 0.15f, -0.39f), new Vector3(0.29f, 0.095f, 0.16f), "tealLight");
            Create(PrimitiveType.Sphere, "Shorts", body, new Vector3(0f, 1.10f, 0.01f), new Vector3(0.78f, 0.48f, 0.47f), "charcoal");
            Create(PrimitiveType.Sphere, "Shirt", body, new Vector3(0f, 1.68f, -0.08f), new Vector3(0.66f, 0.72f, 0.40f), "cream");
            Create(PrimitiveType.Sphere, "LeftJacketPanel", body, new Vector3(-0.24f, 1.73f, -0.01f), new Vector3(0.48f, 0.74f, 0.46f), "teal");
            Create(PrimitiveType.Sphere, "RightJacketPanel", body, new Vector3(0.24f, 1.73f, -0.01f), new Vector3(0.48f, 0.74f, 0.46f), "teal");
            Create(PrimitiveType.Cube, "ShirtOpening", body, new Vector3(0f, 1.70f, -0.47f), new Vector3(0.22f, 0.62f, 0.025f), "cream");
            Create(PrimitiveType.Cube, "Zipper", body, new Vector3(0f, 1.70f, -0.495f), new Vector3(0.018f, 0.61f, 0.012f), "silver");
            Transform leftCollar = Create(PrimitiveType.Cube, "LeftCollar", body, new Vector3(-0.15f, 2.00f, -0.45f), new Vector3(0.25f, 0.10f, 0.05f), "tealLight");
            leftCollar.localRotation = Quaternion.Euler(0f, 0f, -18f);
            Transform rightCollar = Create(PrimitiveType.Cube, "RightCollar", body, new Vector3(0.15f, 2.00f, -0.45f), new Vector3(0.25f, 0.10f, 0.05f), "tealLight");
            rightCollar.localRotation = Quaternion.Euler(0f, 0f, 18f);

            leftArm = BuildArm("Left", body, new Vector3(-0.62f, 1.73f, 0f), 9f);
            rightArm = BuildArm("Right", body, new Vector3(0.62f, 1.73f, 0f), -9f);
            Create(PrimitiveType.Capsule, "Neck", body, new Vector3(0f, 2.18f, 0f), new Vector3(0.23f, 0.20f, 0.24f), "skin");

            head = new GameObject("HeadMotion").transform;
            head.SetParent(body, false);
            head.localPosition = new Vector3(0f, 2.78f, 0f);
            Create(PrimitiveType.Sphere, "Head", head, Vector3.zero, new Vector3(1.18f, 1.06f, 0.98f), "skin");
            Create(PrimitiveType.Sphere, "LeftEar", head, new Vector3(-0.62f, -0.01f, -0.01f), new Vector3(0.27f, 0.34f, 0.22f), "skin");
            Create(PrimitiveType.Sphere, "RightEar", head, new Vector3(0.62f, -0.01f, -0.01f), new Vector3(0.27f, 0.34f, 0.22f), "skin");
            Create(PrimitiveType.Sphere, "LeftEarInner", head, new Vector3(-0.635f, -0.015f, -0.125f), new Vector3(0.12f, 0.18f, 0.055f), "skinRoseSoft");
            Create(PrimitiveType.Sphere, "RightEarInner", head, new Vector3(0.635f, -0.015f, -0.125f), new Vector3(0.12f, 0.18f, 0.055f), "skinRoseSoft");
            Create(PrimitiveType.Sphere, "Nose", head, new Vector3(0f, -0.025f, -0.545f), new Vector3(0.13f, 0.105f, 0.10f), "skinRose");

            leftEye = Create(PrimitiveType.Sphere, "LeftEye", head, new Vector3(-0.30f, 0.12f, -0.49f), new Vector3(0.38f, 0.45f, 0.18f), "white");
            rightEye = Create(PrimitiveType.Sphere, "RightEye", head, new Vector3(0.30f, 0.12f, -0.49f), new Vector3(0.38f, 0.45f, 0.18f), "white");
            leftPupil = Create(PrimitiveType.Sphere, "LeftIris", leftEye, new Vector3(0f, 0f, -0.53f), new Vector3(0.40f, 0.37f, 0.20f), "iris");
            rightPupil = Create(PrimitiveType.Sphere, "RightIris", rightEye, new Vector3(0f, 0f, -0.53f), new Vector3(0.40f, 0.37f, 0.20f), "iris");
            Transform leftBlackPupil = Create(PrimitiveType.Sphere, "LeftPupil", leftPupil, new Vector3(0f, -0.02f, -0.53f), new Vector3(0.54f, 0.57f, 0.24f), "pupil");
            Transform rightBlackPupil = Create(PrimitiveType.Sphere, "RightPupil", rightPupil, new Vector3(0f, -0.02f, -0.53f), new Vector3(0.54f, 0.57f, 0.24f), "pupil");
            Create(PrimitiveType.Sphere, "LeftEyeGlint", leftBlackPupil, new Vector3(-0.17f, 0.20f, -0.55f), new Vector3(0.26f, 0.26f, 0.22f), "white");
            Create(PrimitiveType.Sphere, "RightEyeGlint", rightBlackPupil, new Vector3(-0.17f, 0.20f, -0.55f), new Vector3(0.26f, 0.26f, 0.22f), "white");
            leftLid = Create(PrimitiveType.Sphere, "LeftLid", head, new Vector3(-0.30f, 0.345f, -0.605f), new Vector3(0.39f, 0.002f, 0.17f), "skin");
            rightLid = Create(PrimitiveType.Sphere, "RightLid", head, new Vector3(0.30f, 0.345f, -0.605f), new Vector3(0.39f, 0.002f, 0.17f), "skin");
            leftBrow = Create(PrimitiveType.Capsule, "LeftBrow", head, new Vector3(-0.30f, 0.405f, -0.585f), new Vector3(0.055f, 0.20f, 0.052f), "hair");
            leftBrow.localRotation = Quaternion.Euler(0f, 0f, 90f);
            rightBrow = Create(PrimitiveType.Capsule, "RightBrow", head, new Vector3(0.30f, 0.405f, -0.585f), new Vector3(0.055f, 0.20f, 0.052f), "hair");
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

        private Transform BuildArm(string side, Transform parent, Vector3 position, float zRotation)
        {
            Transform root = new GameObject($"{side}ArmMotion").transform;
            root.SetParent(parent, false);
            root.localPosition = position;
            root.localRotation = Quaternion.Euler(0f, 0f, zRotation);
            Create(PrimitiveType.Capsule, $"{side}Sleeve", root, new Vector3(0f, 0.02f, 0f), new Vector3(0.22f, 0.50f, 0.24f), "teal");
            Create(PrimitiveType.Sphere, $"{side}Cuff", root, new Vector3(0f, -0.39f, -0.01f), new Vector3(0.25f, 0.15f, 0.25f), "tealLight");
            Create(PrimitiveType.Sphere, $"{side}Hand", root, new Vector3(0f, -0.53f, -0.02f), new Vector3(0.22f, 0.25f, 0.21f), "skin");
            return root;
        }

        private void BuildStatusPin()
        {
            Vector3 center = new(0.35f, 1.78f, -0.49f);
            Create(PrimitiveType.Sphere, "PinPetalTop", body, center + new Vector3(0f, 0.075f, 0f), new Vector3(0.075f, 0.10f, 0.035f), "white");
            Create(PrimitiveType.Sphere, "PinPetalLeft", body, center + new Vector3(-0.07f, -0.025f, 0f), new Vector3(0.10f, 0.075f, 0.035f), "white");
            Create(PrimitiveType.Sphere, "PinPetalRight", body, center + new Vector3(0.07f, -0.025f, 0f), new Vector3(0.10f, 0.075f, 0.035f), "white");
            statusOrb = Create(PrimitiveType.Sphere, "StateIndicator", body, center + new Vector3(0f, 0f, -0.025f), Vector3.one * 0.075f, "status");
        }

        private void BuildHair()
        {
            Create(PrimitiveType.Sphere, "HairCap", head, new Vector3(0f, 0.42f, 0.07f), new Vector3(1.10f, 0.62f, 0.94f), "hair");
            Vector3[] locks =
            {
                new(-0.43f, 0.61f, -0.27f), new(-0.20f, 0.74f, -0.39f), new(0.05f, 0.76f, -0.43f),
                new(0.30f, 0.67f, -0.36f), new(0.47f, 0.54f, -0.19f), new(-0.02f, 0.56f, -0.54f)
            };
            float[] rotations = { -38f, -24f, -5f, 24f, 42f, 20f };
            Vector3[] scales =
            {
                new(0.22f, 0.31f, 0.20f), new(0.24f, 0.37f, 0.22f), new(0.25f, 0.40f, 0.22f),
                new(0.24f, 0.36f, 0.21f), new(0.21f, 0.29f, 0.19f), new(0.20f, 0.30f, 0.18f)
            };
            for (int i = 0; i < locks.Length; i++)
            {
                Transform hair = Create(PrimitiveType.Capsule, $"HairLock{i + 1}", head, locks[i], scales[i], "hair");
                hair.localRotation = Quaternion.Euler(0f, 0f, rotations[i]);
            }
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
                "skin" => new Color(1.00f, 0.64f, 0.43f),
                "skinRose" => new Color(1.00f, 0.50f, 0.38f),
                "skinRoseSoft" => new Color(0.94f, 0.43f, 0.34f),
                "blush" => new Color(0.96f, 0.42f, 0.36f),
                "hair" => new Color(0.13f, 0.055f, 0.035f),
                "teal" => new Color(0.04f, 0.38f, 0.43f),
                "tealLight" => new Color(0.08f, 0.54f, 0.58f),
                "cream" => new Color(0.96f, 0.91f, 0.82f),
                "charcoal" => new Color(0.10f, 0.11f, 0.13f),
                "silver" => new Color(0.66f, 0.72f, 0.72f),
                "white" => new Color(0.98f, 0.98f, 0.96f),
                "iris" => new Color(0.26f, 0.10f, 0.035f),
                "pupil" => new Color(0.035f, 0.018f, 0.014f),
                "mouth" => new Color(0.25f, 0.025f, 0.025f),
                "mouthLine" => new Color(0.16f, 0.018f, 0.018f),
                "shadow" => new Color(0.08f, 0.12f, 0.14f),
                "backdrop" => new Color(0.035f, 0.13f, 0.15f),
                _ => new Color(0.20f, 0.80f, 0.75f)
            };
            material.color = color;
            if (material.HasProperty("_BaseColor")) material.SetColor("_BaseColor", color);
            material.SetFloat("_Smoothness", key is "hair" or "iris" or "pupil" ? 0.55f : 0.22f);
            materials[key] = material;
            return material;
        }

        private static float Smooth01(float value)
        {
            value = Mathf.Clamp01(value);
            return value * value * (3f - 2f * value);
        }

        private readonly struct VisemeCue
        {
            public readonly float Time;
            public readonly string Name;
            public readonly float Open;
            public readonly float Width;

            public VisemeCue(float time, string name, float open, float width)
            {
                Time = time;
                Name = name;
                Open = open;
                Width = width;
            }
        }
    }
}
