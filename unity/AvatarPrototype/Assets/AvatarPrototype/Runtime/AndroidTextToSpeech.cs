using UnityEngine;

namespace PersonalAssistant.Avatar
{
    internal static class AndroidTextToSpeech
    {
        public static bool Speak(GameObject callbackTarget, string text)
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            try
            {
                using AndroidJavaClass bridge = new("com.personalassistant.avatar.MiloTextToSpeech");
                bridge.CallStatic("speak", callbackTarget.name, text);
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
