using UnityEngine;

namespace PersonalAssistant.Avatar
{
    internal static class AndroidTextToSpeech
    {
        public static bool Speak(GameObject callbackTarget, string text, string voiceStyle = "default")
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            try
            {
                using AndroidJavaClass bridge = new("com.personalassistant.avatar.MiloTextToSpeech");
                bridge.CallStatic("speak", callbackTarget.name, text, voiceStyle);
                return true;
            }
            catch (System.Exception exception)
            {
                Debug.LogWarning($"TTS_BRIDGE_ERROR: {exception.Message}");
                return false;
            }
#else
            return false;
#endif
        }

#if UNITY_ANDROID && !UNITY_EDITOR
        private static AndroidJavaClass positionBridge;
#endif

        /// <summary>Milliseconds of synthesized speech presented to the speaker, or -1 when not playing.</summary>
        public static long GetPlaybackPositionMs()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            try
            {
                positionBridge ??= new AndroidJavaClass("com.personalassistant.avatar.MiloTextToSpeech");
                return positionBridge.CallStatic<long>("getPlaybackPositionMs");
            }
            catch (System.Exception exception)
            {
                Debug.LogWarning($"TTS_POSITION_ERROR: {exception.Message}");
                return -1;
            }
#else
            return -1;
#endif
        }

        public static void Stop()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            try
            {
                using AndroidJavaClass bridge = new("com.personalassistant.avatar.MiloTextToSpeech");
                bridge.CallStatic("stop");
            }
            catch (System.Exception exception)
            {
                Debug.LogWarning($"TTS_STOP_ERROR: {exception.Message}");
            }
#endif
        }

        public static void Shutdown()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            try
            {
                using AndroidJavaClass bridge = new("com.personalassistant.avatar.MiloTextToSpeech");
                bridge.CallStatic("shutdown");
            }
            catch (System.Exception exception)
            {
                Debug.LogWarning($"TTS_SHUTDOWN_ERROR: {exception.Message}");
            }
#endif
        }
    }
}
