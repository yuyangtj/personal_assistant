using System;
using UnityEngine;

namespace PersonalAssistant.Avatar
{
    public enum AvatarMode
    {
        Idle,
        Listening,
        Thinking,
        Speaking,
        Success,
        Error
    }

    public enum AvatarEmotion
    {
        Neutral,
        Warm,
        Curious,
        Excited,
        Concerned
    }

    [Serializable]
    public sealed class AvatarCommand
    {
        public string mode = "Idle";
        public string emotion = "Neutral";
        [Range(0f, 1f)] public float intensity = 0.5f;
        public string speechCue;
    }

    /// <summary>
    /// Provider-neutral reply contract delivered by the Android host. A stable
    /// response ID prevents event polling from speaking the same reply twice.
    /// </summary>
    [Serializable]
    public sealed class AssistantResponse
    {
        public string responseId;
        public string text;
        public string emotion = "Warm";
        [Range(0f, 1f)] public float intensity = 0.65f;
    }
}
