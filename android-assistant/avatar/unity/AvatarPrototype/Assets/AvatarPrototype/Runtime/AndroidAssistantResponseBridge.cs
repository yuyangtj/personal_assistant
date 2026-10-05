using UnityEngine;

namespace PersonalAssistant.Avatar
{
    internal static class AndroidAssistantResponseBridge
    {
        public static bool Deliver(GameObject callbackTarget, string responseJson)
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            try
            {
                using AndroidJavaClass bridge = new("com.personalassistant.avatar.MiloAssistantBridge");
                bridge.CallStatic("deliverResponse", callbackTarget.name, responseJson);
                return true;
            }
            catch (System.Exception exception)
            {
                Debug.LogWarning($"ASSISTANT_BRIDGE_ERROR: {exception.Message}");
                return false;
            }
#else
            return false;
#endif
        }
    }
}
