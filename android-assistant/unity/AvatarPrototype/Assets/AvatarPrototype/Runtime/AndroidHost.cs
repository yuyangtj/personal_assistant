using UnityEngine;

namespace PersonalAssistant.Avatar
{
    /// <summary>Talks to a native Android app that embeds Unity (see MiloHost.java).</summary>
    internal static class AndroidHost
    {
        private static bool? embedded;

        /// <summary>True when a native shell hosts Unity; false in the editor and the standalone APK.</summary>
        public static bool IsEmbedded
        {
            get
            {
                if (embedded.HasValue) return embedded.Value;
#if UNITY_ANDROID && !UNITY_EDITOR
                try
                {
                    using AndroidJavaClass host = new("com.personalassistant.avatar.MiloHost");
                    embedded = host.CallStatic<bool>("isEmbedded");
                }
                catch (System.Exception exception)
                {
                    Debug.LogWarning($"HOST_BRIDGE_ERROR: {exception.Message}");
                    embedded = false;
                }
#else
                embedded = false;
#endif
                return embedded.Value;
            }
        }

        public static void NotifySpeechStarted(string responseId) => Notify("notifySpeechStarted", responseId, null);

        public static void NotifySpeechFinished(string responseId, string reason) => Notify("notifySpeechFinished", responseId, reason);

        private static void Notify(string method, string responseId, string reason)
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            if (!IsEmbedded) return;
            try
            {
                using AndroidJavaClass host = new("com.personalassistant.avatar.MiloHost");
                if (reason == null) host.CallStatic(method, responseId ?? string.Empty);
                else host.CallStatic(method, responseId ?? string.Empty, reason);
            }
            catch (System.Exception exception)
            {
                Debug.LogWarning($"HOST_NOTIFY_ERROR: {exception.Message}");
            }
#endif
        }
    }
}
