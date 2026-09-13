using UnityEngine;

namespace PersonalAssistant.Avatar
{
    public sealed class PolishedPrototypeHud : MonoBehaviour
    {
        private ProceduralAvatarController avatar;
        private PrototypeDemo demo;
        private Texture2D panelTexture;
        private Texture2D dockTexture;
        private Texture2D buttonTexture;
        private Texture2D selectedTexture;
        private Texture2D pressedTexture;
        private Texture2D whiteDotTexture;

        private static readonly AvatarMode[] Modes =
        {
            AvatarMode.Idle, AvatarMode.Listening, AvatarMode.Thinking,
            AvatarMode.Speaking, AvatarMode.Success, AvatarMode.Error
        };

        private static readonly AvatarEmotion[] Emotions =
        {
            AvatarEmotion.Warm, AvatarEmotion.Curious, AvatarEmotion.Curious,
            AvatarEmotion.Excited, AvatarEmotion.Warm, AvatarEmotion.Concerned
        };

        private void Awake()
        {
            avatar = GetComponent<ProceduralAvatarController>();
            demo = GetComponent<PrototypeDemo>();
            CreateUiTextures();
        }

        private void OnGUI()
        {
            if (avatar == null || demo == null) return;
            EnsureUiTextures();

            float scale = Mathf.Clamp(Screen.width / 1080f, 0.72f, 1.35f);
            Matrix4x4 previousMatrix = GUI.matrix;
            Color previousColor = GUI.color;
            GUI.matrix = Matrix4x4.Scale(Vector3.one * scale);

            float width = Screen.width / scale;
            float height = Screen.height / scale;
            float safeTop = Mathf.Max(22f, (Screen.height - Screen.safeArea.yMax) / scale + 18f);
            float safeBottom = Mathf.Max(18f, Screen.safeArea.yMin / scale + 18f);

            GUIStyle panel = PanelStyle(panelTexture);
            GUIStyle dock = PanelStyle(dockTexture);
            GUIStyle title = LabelStyle(27, FontStyle.Bold, Color.white);
            GUIStyle eyebrow = LabelStyle(12, FontStyle.Bold, new Color(0.42f, 0.88f, 0.88f));
            GUIStyle detail = LabelStyle(17, FontStyle.Normal, new Color(0.78f, 0.88f, 0.90f));
            GUIStyle captionStyle = LabelStyle(20, FontStyle.Bold, Color.white);
            captionStyle.alignment = TextAnchor.MiddleCenter;
            GUIStyle button = ButtonStyle(buttonTexture);
            GUIStyle selected = ButtonStyle(selectedTexture);

            Rect header = new(28f, safeTop, 350f, 126f);
            GUI.Box(header, GUIContent.none, panel);
            GUI.Label(new Rect(header.x + 24f, header.y + 16f, 250f, 20f), "PERSONAL ASSISTANT", eyebrow);
            GUI.Label(new Rect(header.x + 24f, header.y + 38f, 230f, 38f), "Milo", title);

            GUI.color = ModeColor(avatar.Mode);
            GUI.DrawTexture(new Rect(header.x + 25f, header.y + 86f, 15f, 15f), whiteDotTexture, ScaleMode.StretchToFill, true);
            GUI.color = previousColor;
            GUI.Label(new Rect(header.x + 50f, header.y + 79f, 270f, 28f), $"{avatar.Mode}  ·  {avatar.Emotion}", detail);

            GUIStyle autoStyle = demo.AutoDemo ? selected : button;
            if (GUI.Button(new Rect(width - 214f, safeTop + 10f, 184f, 48f), demo.AutoDemo ? "AUTO  •  ON" : "AUTO  •  OFF", autoStyle))
                demo.ToggleAutoDemo();

            string caption = avatar.Mode switch
            {
                AvatarMode.Listening => "I’m listening…",
                AvatarMode.Thinking => "Let me think about that.",
                AvatarMode.Speaking when !string.IsNullOrEmpty(avatar.ActiveSpeechText) => avatar.ActiveSpeechText,
                AvatarMode.Speaking => "Hello! I’m ready to help you.",
                AvatarMode.Success => "Done — everything worked.",
                AvatarMode.Error => "I hit a problem. Let’s try again.",
                _ => "What would you like to do?"
            };

            float dockWidth = Mathf.Min(940f, width - 48f);
            float dockX = (width - dockWidth) * 0.5f;
            float dockY = height - safeBottom - 154f;
            GUI.Box(new Rect(dockX, dockY, dockWidth, 154f), GUIContent.none, dock);
            GUI.Label(new Rect(dockX + 24f, dockY + 14f, dockWidth - 48f, 38f), caption, captionStyle);

            float gap = 10f;
            float buttonWidth = (dockWidth - 48f - gap * 5f) / 6f;
            for (int i = 0; i < Modes.Length; i++)
            {
                GUIStyle modeStyle = avatar.Mode == Modes[i] ? selected : button;
                if (GUI.Button(new Rect(dockX + 24f + i * (buttonWidth + gap), dockY + 69f, buttonWidth, 58f), Modes[i].ToString(), modeStyle))
                    demo.SelectMode(Modes[i], Emotions[i]);
            }

            if (avatar.Mode == AvatarMode.Speaking)
            {
                GUIStyle viseme = LabelStyle(13, FontStyle.Bold, new Color(0.56f, 0.94f, 0.82f));
                viseme.alignment = TextAnchor.MiddleRight;
                string syncLabel = avatar.IsEnglishSpeechActive ? "TTS SYNC" : "DEMO";
                GUI.Label(new Rect(width - 260f, safeTop + 68f, 230f, 24f), $"{syncLabel}  ·  {avatar.ActiveViseme}", viseme);
            }

            GUI.color = previousColor;
            GUI.matrix = previousMatrix;
        }

        private void CreateUiTextures()
        {
            panelTexture = RoundedTexture(new Color(0.025f, 0.055f, 0.068f, 0.92f), 18);
            dockTexture = RoundedTexture(new Color(0.018f, 0.038f, 0.049f, 0.94f), 22);
            buttonTexture = RoundedTexture(new Color(0.09f, 0.14f, 0.16f, 0.98f), 16);
            selectedTexture = RoundedTexture(new Color(0.045f, 0.47f, 0.48f, 1f), 16);
            pressedTexture = RoundedTexture(new Color(0.08f, 0.62f, 0.59f, 1f), 16);
            whiteDotTexture = RoundedTexture(Color.white, 30);
        }

        private void EnsureUiTextures()
        {
            if (panelTexture == null) CreateUiTextures();
        }

        private GUIStyle PanelStyle(Texture2D background)
        {
            return new GUIStyle(GUI.skin.box) { normal = { background = background }, border = new RectOffset(20, 20, 20, 20) };
        }

        private GUIStyle ButtonStyle(Texture2D background)
        {
            GUIStyle style = new(GUI.skin.button)
            {
                fontSize = 16,
                fontStyle = FontStyle.Bold,
                alignment = TextAnchor.MiddleCenter,
                border = new RectOffset(18, 18, 18, 18),
                padding = new RectOffset(8, 8, 6, 6)
            };
            style.normal.background = background;
            style.hover.background = background;
            style.focused.background = background;
            style.active.background = pressedTexture;
            style.normal.textColor = new Color(0.88f, 0.94f, 0.95f);
            style.hover.textColor = Color.white;
            style.active.textColor = Color.white;
            return style;
        }

        private static GUIStyle LabelStyle(int size, FontStyle fontStyle, Color color)
        {
            return new GUIStyle(GUI.skin.label) { fontSize = size, fontStyle = fontStyle, normal = { textColor = color } };
        }

        private static Texture2D RoundedTexture(Color color, int radius)
        {
            const int size = 64;
            Texture2D texture = new(size, size, TextureFormat.RGBA32, false)
            {
                filterMode = FilterMode.Bilinear,
                wrapMode = TextureWrapMode.Clamp,
                hideFlags = HideFlags.DontSave
            };
            Color[] pixels = new Color[size * size];
            for (int y = 0; y < size; y++)
            {
                for (int x = 0; x < size; x++)
                {
                    float dx = Mathf.Max(radius - x, 0f, x - (size - radius - 1));
                    float dy = Mathf.Max(radius - y, 0f, y - (size - radius - 1));
                    pixels[y * size + x] = dx * dx + dy * dy <= radius * radius ? color : Color.clear;
                }
            }
            texture.SetPixels(pixels);
            texture.Apply(false, true);
            return texture;
        }

        private static Color ModeColor(AvatarMode mode)
        {
            return mode switch
            {
                AvatarMode.Listening => new Color(0.20f, 0.85f, 0.95f),
                AvatarMode.Thinking => new Color(0.66f, 0.42f, 1.00f),
                AvatarMode.Speaking => new Color(0.16f, 0.92f, 0.70f),
                AvatarMode.Success => new Color(0.30f, 0.95f, 0.46f),
                AvatarMode.Error => new Color(1.00f, 0.35f, 0.34f),
                _ => new Color(0.22f, 0.65f, 0.72f)
            };
        }

        private void OnDestroy()
        {
            Destroy(panelTexture);
            Destroy(dockTexture);
            Destroy(buttonTexture);
            Destroy(selectedTexture);
            Destroy(pressedTexture);
            Destroy(whiteDotTexture);
        }
    }
}
