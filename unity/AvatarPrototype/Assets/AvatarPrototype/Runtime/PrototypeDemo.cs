using UnityEngine;

namespace PersonalAssistant.Avatar
{
    public sealed class PrototypeDemo : MonoBehaviour
    {
        [SerializeField] private ProceduralAvatarController avatar;
        [SerializeField] private bool autoDemo = true;
        private float startedAt;
        private int lastStage = -1;
        private int mockResponseSequence;

        public bool AutoDemo => autoDemo;

        private readonly AvatarMode[] modes =
        {
            AvatarMode.Idle, AvatarMode.Listening, AvatarMode.Thinking,
            AvatarMode.Speaking, AvatarMode.Success, AvatarMode.Error
        };

        private readonly AvatarEmotion[] emotions =
        {
            AvatarEmotion.Warm, AvatarEmotion.Curious, AvatarEmotion.Curious,
            AvatarEmotion.Excited, AvatarEmotion.Warm, AvatarEmotion.Concerned
        };

        private void Awake()
        {
            QualitySettings.vSyncCount = 0;
            Application.targetFrameRate = 60;
            if (avatar == null) avatar = FindFirstObjectByType<ProceduralAvatarController>();
            startedAt = Time.time;
            if (GetComponent<PolishedPrototypeHud>() == null)
                gameObject.AddComponent<PolishedPrototypeHud>();
        }

        private void Update()
        {
            if (!autoDemo || avatar == null) return;
            int stage = Mathf.FloorToInt(Mathf.Repeat(Time.time - startedAt, 24f) / 4f);
            if (stage == lastStage) return;
            lastStage = stage;
            avatar.ApplyCommand(modes[stage], emotions[stage], stage == 3 ? 0.8f : 0.55f);
        }

        public void ToggleAutoDemo()
        {
            autoDemo = !autoDemo;
            startedAt = Time.time;
            lastStage = -1;
        }

        public void SelectMode(AvatarMode mode, AvatarEmotion emotion)
        {
            autoDemo = false;
            if (mode == AvatarMode.Speaking)
                DeliverMockAssistantResponse();
            else
                avatar.ApplyCommand(mode, emotion, 0.65f);
        }

        private void DeliverMockAssistantResponse()
        {
            AssistantResponse response = new()
            {
                responseId = $"prototype-{++mockResponseSequence}",
                text = "Hello! I'm Milo. How can I help you today?",
                emotion = AvatarEmotion.Excited.ToString(),
                intensity = 0.72f
            };
            string json = JsonUtility.ToJson(response);
            if (!AndroidAssistantResponseBridge.Deliver(avatar.gameObject, json))
                avatar.ApplyAssistantResponseJson(json);
        }
    }
}
