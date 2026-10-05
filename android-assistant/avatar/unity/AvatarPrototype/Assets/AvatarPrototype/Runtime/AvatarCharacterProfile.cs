using UnityEngine;

namespace PersonalAssistant.Avatar
{
    public enum AvatarCharacter
    {
        CoolMan,
        Milo
    }

    public enum GestureSpace
    {
        /// <summary>Euler offsets in each bone's own axes (the Blender-generated Milo rig).</summary>
        BoneLocal,
        /// <summary>Euler offsets in character space, independent of the rig's bone axes.</summary>
        Character
    }

    /// <summary>
    /// Everything the controller needs to drive one rigged character. Both characters expose
    /// the same facial blendshape names, so speech, blinking and expressions are shared.
    /// </summary>
    public sealed class AvatarCharacterProfile
    {
        public AvatarCharacter Character { get; private set; }
        public string DisplayName { get; private set; }
        public string ResourcePath { get; private set; }
        public string FaceRenderer { get; private set; }
        public string LeftEyeBone { get; private set; } = "Eye.L";
        public string RightEyeBone { get; private set; } = "Eye.R";
        /// <summary>The arm shown on the left of the screen.</summary>
        public string ScreenLeftArm { get; private set; }
        public string ScreenRightArm { get; private set; }
        public bool UseClayShader { get; private set; }
        public bool CastShadows { get; private set; }
        public float GazeScale { get; private set; }
        /// <summary>How much loudness-driven jawOpen is layered on top of the visemes.</summary>
        public float JawOpenScale { get; private set; } = 0.55f;
        public GestureSpace Gestures { get; private set; }
        public Vector3 CameraPosition { get; private set; }
        public Vector3 CameraTarget { get; private set; }
        public float CameraFieldOfView { get; private set; }
        public string IdlePoseClip { get; private set; }
        public string GreetingClip { get; private set; }
        public string SuccessClip { get; private set; }
        public string Credit { get; private set; }
        /// <summary>Android TTS voice style: "male" or "default".</summary>
        public string VoiceStyle { get; private set; } = "default";

        public static readonly AvatarCharacterProfile CoolMan = new()
        {
            Character = AvatarCharacter.CoolMan,
            DisplayName = "REAL",
            ResourcePath = "Character/CoolManRig",
            FaceRenderer = "Assistant_Face",
            ScreenLeftArm = "UpperArm.R",
            ScreenRightArm = "UpperArm.L",
            UseClayShader = false,
            CastShadows = true,
            GazeScale = 0.025f,
            JawOpenScale = 0.25f,
            Gestures = GestureSpace.Character,
            CameraPosition = new Vector3(0f, 1.57f, -2.05f),
            CameraTarget = new Vector3(0f, 1.50f, 0f),
            CameraFieldOfView = 30f,
            IdlePoseClip = "shakehand",
            GreetingClip = "salute",
            SuccessClip = "shakehand",
            Credit = "\"Cool Man\" by ardhanaputra · CC BY 4.0 · modified",
            VoiceStyle = "male"
        };

        public static readonly AvatarCharacterProfile Milo = new()
        {
            Character = AvatarCharacter.Milo,
            DisplayName = "MILO",
            ResourcePath = "Character/MiloRig",
            FaceRenderer = "Milo_Face",
            ScreenLeftArm = "UpperArm.L",
            ScreenRightArm = "UpperArm.R",
            UseClayShader = true,
            CastShadows = false,
            GazeScale = 0.42f,
            Gestures = GestureSpace.BoneLocal,
            CameraPosition = new Vector3(0f, 2.2f, -7.3f),
            CameraTarget = new Vector3(0f, 1.9f, 0f),
            CameraFieldOfView = 46f
        };

        public static AvatarCharacterProfile For(AvatarCharacter character) =>
            character == AvatarCharacter.Milo ? Milo : CoolMan;
    }
}
