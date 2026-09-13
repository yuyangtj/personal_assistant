using UnityEngine;

namespace PersonalAssistant.Avatar
{
    public sealed class PrototypeDemo : MonoBehaviour
    {
        [SerializeField] private ProceduralAvatarController avatar;
        [SerializeField] private bool autoDemo = true;
        private float startedAt;
        private int lastStage = -1;

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
        }

        private void Update()
        {
            if (!autoDemo || avatar == null) return;
            int stage = Mathf.FloorToInt(Mathf.Repeat(Time.time - startedAt, 24f) / 4f);
            if (stage == lastStage) return;
            lastStage = stage;
            avatar.ApplyCommand(modes[stage], emotions[stage], stage == 3 ? 0.8f : 0.55f);
        }

        private void OnGUI()
        {
            if (avatar == null) return;
            float scale = Mathf.Clamp(Screen.width / 1080f, 0.72f, 1.35f);
            Matrix4x4 previous = GUI.matrix;
            GUI.matrix = Matrix4x4.Scale(Vector3.one * scale);

            float width = Screen.width / scale;
            float height = Screen.height / scale;
            GUIStyle title = new(GUI.skin.label) { fontSize = 28, fontStyle = FontStyle.Bold, normal = { textColor = Color.white } };
            GUIStyle detail = new(GUI.skin.label) { fontSize = 18, normal = { textColor = new Color(0.78f, 0.88f, 0.90f) } };
            GUIStyle button = new(GUI.skin.button) { fontSize = 16, fontStyle = FontStyle.Bold };

            GUI.Box(new Rect(24f, 24f, 286f, 128f), GUIContent.none);
            GUI.Label(new Rect(44f, 38f, 250f, 40f), "Milo · visual spike", title);
            GUI.Label(new Rect(44f, 80f, 250f, 28f), $"{avatar.Mode} · {avatar.Emotion}", detail);
            GUI.Label(new Rect(44f, 108f, 250f, 28f), avatar.Mode == AvatarMode.Speaking ? $"viseme: {avatar.ActiveViseme}" : "animation contract active", detail);

            string caption = avatar.Mode switch
            {
                AvatarMode.Listening => "I’m listening…",
                AvatarMode.Thinking => "Let me think about that.",
                AvatarMode.Speaking => "Hello! I’m ready to help you.",
                AvatarMode.Success => "Done — everything worked.",
                AvatarMode.Error => "I hit a problem. Let’s try again.",
                _ => "What would you like to do?"
            };
            GUI.Box(new Rect(width * 0.5f - 260f, height - 178f, 520f, 58f), caption);

            float y = height - 102f;
            float totalWidth = 6 * 132f;
            float x = (width - totalWidth) * 0.5f;
            for (int i = 0; i < modes.Length; i++)
            {
                if (GUI.Button(new Rect(x + i * 132f, y, 122f, 54f), modes[i].ToString(), button))
                {
                    autoDemo = false;
                    avatar.ApplyCommand(modes[i], emotions[i], 0.65f);
                }
            }

            string demoText = autoDemo ? "AUTO DEMO: ON" : "AUTO DEMO: OFF";
            if (GUI.Button(new Rect(width - 202f, 34f, 174f, 44f), demoText, button))
            {
                autoDemo = !autoDemo;
                startedAt = Time.time;
                lastStage = -1;
            }

            GUI.matrix = previous;
        }
    }
}
