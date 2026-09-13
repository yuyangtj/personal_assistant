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
        private bool usingRiggedAsset;
        private SkinnedMeshRenderer riggedMouthRenderer;
        private Transform riggedTeeth;
        private Transform riggedTongue;
        private Vector3 leftPupilBasePosition;
        private Vector3 rightPupilBasePosition;
        private Vector3 leftLidBasePosition;
        private Vector3 rightLidBasePosition;
        private Vector3 leftLidBaseScale;
        private Vector3 rightLidBaseScale;
        private Vector3 leftBrowBasePosition;
        private Vector3 rightBrowBasePosition;
        private Quaternion leftBrowBaseRotation;
        private Quaternion rightBrowBaseRotation;

        private Vector3 bodyBasePosition;
        private Vector3 headBasePosition;
        private Quaternion headBaseRotation;
        private Quaternion leftArmBaseRotation;
        private Quaternion rightArmBaseRotation;
        private Vector3 statusOrbBaseScale;
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
            statusOrbBaseScale = statusOrb.localScale;
            if (usingRiggedAsset) CaptureRiggedDefaults();
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
                EvaluateViseme(Mathf.Repeat(t - speechStartedAt, 4.12f), out mouthOpen, out float visemeWidth);
                mouthWidth *= visemeWidth;
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

        private void CaptureRiggedDefaults()
        {
            leftPupilBasePosition = leftPupil.localPosition;
            rightPupilBasePosition = rightPupil.localPosition;
            leftLidBasePosition = leftLid.localPosition;
            rightLidBasePosition = rightLid.localPosition;
            leftLidBaseScale = leftLid.localScale;
            rightLidBaseScale = rightLid.localScale;
            leftBrowBasePosition = leftBrow.localPosition;
            rightBrowBasePosition = rightBrow.localPosition;
            leftBrowBaseRotation = leftBrow.localRotation;
            rightBrowBaseRotation = rightBrow.localRotation;
        }

        private void UpdateRiggedFace(float t)
        {
            float browTilt = 0f;
            float browHeight = 0f;
            string activeShape = "viseme_sil";
            float activeWeight = 88f;

            switch (Emotion)
            {
                case AvatarEmotion.Warm:
                    activeShape = "expression_smile";
                    activeWeight = 72f;
                    browHeight = 0.018f;
                    break;
                case AvatarEmotion.Curious:
                    browTilt = 10f;
                    browHeight = 0.035f;
                    break;
                case AvatarEmotion.Excited:
                    activeShape = "expression_smile";
                    activeWeight = 100f;
                    browHeight = 0.055f;
                    break;
                case AvatarEmotion.Concerned:
                    activeShape = "expression_concerned";
                    activeWeight = 90f;
                    browTilt = -12f;
                    browHeight = 0.020f;
                    break;
            }

            float mouthOpen = 0.08f;
            if (Mode == AvatarMode.Speaking)
            {
                EvaluateViseme(Mathf.Repeat(t - speechStartedAt, 4.12f), out mouthOpen, out _);
                activeShape = ResolveRiggedViseme(ActiveViseme);
                activeWeight = Mathf.Lerp(68f, 100f, Intensity);
            }
            else if (Mode == AvatarMode.Success)
            {
                activeShape = "viseme_aa";
                activeWeight = 72f;
                mouthOpen = 0.45f;
            }
            else
            {
                ActiveViseme = "sil";
            }

            if (riggedMouthRenderer != null && riggedMouthRenderer.sharedMesh != null)
            {
                Mesh mesh = riggedMouthRenderer.sharedMesh;
                for (int i = 0; i < mesh.blendShapeCount; i++)
                {
                    float target = mesh.GetBlendShapeName(i) == activeShape ? activeWeight : 0f;
                    float current = riggedMouthRenderer.GetBlendShapeWeight(i);
                    riggedMouthRenderer.SetBlendShapeWeight(i, Mathf.Lerp(current, target, Time.deltaTime * 14f));
                }
            }

            if (riggedTongue != null)
                riggedTongue.gameObject.SetActive(Mode == AvatarMode.Speaking || Mode == AvatarMode.Success);
            if (riggedTeeth != null)
                riggedTeeth.localScale = Vector3.Lerp(riggedTeeth.localScale, new Vector3(1f, Mathf.Lerp(0.45f, 1f, mouthOpen), 1f), Time.deltaTime * 12f);

            leftBrow.localPosition = Vector3.Lerp(leftBrow.localPosition, leftBrowBasePosition + Vector3.up * browHeight, Time.deltaTime * 8f);
            rightBrow.localPosition = Vector3.Lerp(rightBrow.localPosition, rightBrowBasePosition + Vector3.up * browHeight, Time.deltaTime * 8f);
            leftBrow.localRotation = Quaternion.Slerp(leftBrow.localRotation, leftBrowBaseRotation * Quaternion.Euler(0f, 0f, -browTilt), Time.deltaTime * 8f);
            rightBrow.localRotation = Quaternion.Slerp(rightBrow.localRotation, rightBrowBaseRotation * Quaternion.Euler(0f, 0f, browTilt), Time.deltaTime * 8f);

            float lidStretch = Mathf.Lerp(1f, 18f, blinkAmount);
            float lidDrop = Mathf.Lerp(0f, 0.19f, blinkAmount);
            bool lidsVisible = blinkAmount > 0.012f;
            leftLid.gameObject.SetActive(lidsVisible);
            rightLid.gameObject.SetActive(lidsVisible);
            leftLid.localPosition = leftLidBasePosition + Vector3.down * lidDrop;
            rightLid.localPosition = rightLidBasePosition + Vector3.down * lidDrop;
            leftLid.localScale = new Vector3(leftLidBaseScale.x, leftLidBaseScale.y * lidStretch, leftLidBaseScale.z);
            rightLid.localScale = new Vector3(rightLidBaseScale.x, rightLidBaseScale.y * lidStretch, rightLidBaseScale.z);
        }

        private static string ResolveRiggedViseme(string viseme)
        {
            return viseme switch
            {
                "HH" => "viseme_aa",
                "LL" => "viseme_nn",
                "sil" => "viseme_sil",
                _ => $"viseme_{viseme}"
            };
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
                leftPupil.localPosition = leftPupilBasePosition + new Vector3(gaze.x, gaze.y, 0f);
                rightPupil.localPosition = rightPupilBasePosition + new Vector3(gaze.x, gaze.y, 0f);
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
            renderer.SetPropertyBlock(block);
            float pulse = 1f + Mathf.Sin(t * (Mode == AvatarMode.Thinking ? 4.5f : 2.2f)) * 0.08f;
            statusOrb.localScale = statusOrbBaseScale * pulse;
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
            if (TryBuildRiggedAvatar()) return;

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

        private bool TryBuildRiggedAvatar()
        {
            GameObject prefab = Resources.Load<GameObject>("Character/MiloRig");
            if (prefab == null) return false;

            GameObject instance = Instantiate(prefab, body);
            instance.name = "MiloRiggedCharacter";
            instance.transform.localPosition = Vector3.zero;
            instance.transform.localRotation = Quaternion.Euler(0f, 180f, 0f);
            instance.transform.localScale = Vector3.one;

            head = FindDescendant(instance.transform, "Head");
            leftEye = FindDescendant(instance.transform, "White_Eye.L");
            rightEye = FindDescendant(instance.transform, "White_Eye.R");
            leftPupil = FindDescendant(instance.transform, "Iris_Eye.L");
            rightPupil = FindDescendant(instance.transform, "Iris_Eye.R");
            leftLid = FindDescendant(instance.transform, "Lid_Eye.L");
            rightLid = FindDescendant(instance.transform, "Lid_Eye.R");
            leftBrow = FindDescendant(instance.transform, "Brow.L");
            rightBrow = FindDescendant(instance.transform, "Brow.R");
            mouth = FindDescendant(instance.transform, "Mouth_Interior");
            leftMouthCorner = FindDescendant(instance.transform, "MouthCorner.L");
            rightMouthCorner = FindDescendant(instance.transform, "MouthCorner.R");
            leftCheek = FindDescendant(instance.transform, "Blush.L");
            rightCheek = FindDescendant(instance.transform, "Blush.R");
            leftArm = FindDescendant(instance.transform, "UpperArm.L");
            rightArm = FindDescendant(instance.transform, "UpperArm.R");
            statusOrb = FindDescendant(instance.transform, "Status_Orb");
            riggedTeeth = FindDescendant(instance.transform, "Mouth_Teeth");
            riggedTongue = FindDescendant(instance.transform, "Mouth_Tongue");

            Transform[] required = { head, leftEye, rightEye, leftPupil, rightPupil, leftLid, rightLid, leftBrow, rightBrow, mouth, leftArm, rightArm, statusOrb };
            for (int i = 0; i < required.Length; i++)
            {
                if (required[i] != null) continue;
                Debug.LogWarning("Rigged avatar is incomplete; using procedural fallback.");
                if (Application.isPlaying) Destroy(instance);
                else DestroyImmediate(instance);
                return false;
            }

            riggedMouthRenderer = mouth.GetComponent<SkinnedMeshRenderer>();
            ApplyRiggedMaterials(instance);
            usingRiggedAsset = true;
            Debug.Log($"RIGGED_AVATAR_ACTIVE: renderers={instance.GetComponentsInChildren<Renderer>(true).Length}, mouthBlendShapes={(riggedMouthRenderer == null ? 0 : riggedMouthRenderer.sharedMesh.blendShapeCount)}");
            return true;
        }

        private void ApplyRiggedMaterials(GameObject instance)
        {
            foreach (Renderer renderer in instance.GetComponentsInChildren<Renderer>(true))
            {
                string objectName = renderer.gameObject.name;
                string key = objectName switch
                {
                    _ when objectName.StartsWith("Skin_EarInner") => "skinRoseSoft",
                    _ when objectName.StartsWith("Skin_") => "skin",
                    _ when objectName.StartsWith("Hair_") || objectName.StartsWith("Brow") => "hair",
                    _ when objectName.StartsWith("Shirt_") && (objectName.Contains("Rib") || objectName.Contains("Hem") || objectName.Contains("Cuff")) => "shirtRedDark",
                    _ when objectName.StartsWith("Shirt_") => "shirtRed",
                    _ when objectName.StartsWith("Shorts_") => "shortsBlue",
                    _ when objectName.StartsWith("Shoe_Sole") => "shoeSole",
                    _ when objectName.StartsWith("Shoe_") => "shoeYellow",
                    _ when objectName.StartsWith("Sock_") || objectName.StartsWith("White_") || objectName.StartsWith("Glint_") || objectName.StartsWith("Status_Petal") || objectName == "Mouth_Teeth" => "white",
                    _ when objectName.StartsWith("Iris_") => "iris",
                    _ when objectName.StartsWith("Pupil_") => "pupil",
                    _ when objectName.StartsWith("Blush") => "blush",
                    _ when objectName == "Mouth_Tongue" => "tongue",
                    _ when objectName.StartsWith("Mouth") => "mouth",
                    _ when objectName == "Status_Orb" => "status",
                    _ => null
                };
                if (key == null) continue;
                Material material = GetMaterial(key);
                renderer.sharedMaterial = material;
                Color color = material.HasProperty("_BaseColor") ? material.GetColor("_BaseColor") : material.color;
                MaterialPropertyBlock block = new();
                renderer.GetPropertyBlock(block);
                block.SetColor("_BaseColor", color);
                block.SetColor("_Color", color);
                renderer.SetPropertyBlock(block);
                bool facialOverlay = objectName.StartsWith("Brow") || objectName.StartsWith("Lid_") ||
                                     objectName.StartsWith("White_Eye") || objectName.StartsWith("Iris_") ||
                                     objectName.StartsWith("Pupil_") || objectName.StartsWith("Glint_") ||
                                     objectName.StartsWith("Blush") || objectName.StartsWith("Mouth") ||
                                     objectName == "Skin_Nose";
                renderer.shadowCastingMode = facialOverlay ? ShadowCastingMode.Off : ShadowCastingMode.On;
                renderer.receiveShadows = !facialOverlay;
            }
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
                "shadow" => new Color(0.08f, 0.12f, 0.14f),
                "backdrop" => new Color(0.035f, 0.13f, 0.15f),
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
