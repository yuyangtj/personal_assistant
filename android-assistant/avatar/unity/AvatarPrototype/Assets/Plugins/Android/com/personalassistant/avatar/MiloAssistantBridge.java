package com.personalassistant.avatar;

import android.util.Log;

import com.unity3d.player.UnityPlayer;

/** Entry point used by the future Kotlin host after it receives assistant text. */
public final class MiloAssistantBridge {
    private static final String TAG = "MiloBridge";
    private static final String DEFAULT_TARGET = "AvatarRuntime";

    private MiloAssistantBridge() { }

    public static void deliverResponse(String responseJson) {
        deliverResponse(DEFAULT_TARGET, responseJson);
    }

    public static void deliverResponse(String gameObjectName, String responseJson) {
        if (gameObjectName == null || gameObjectName.isEmpty() || responseJson == null || responseJson.isEmpty()) {
            Log.w(TAG, "Ignored empty assistant response");
            return;
        }
        UnityPlayer.currentActivity.runOnUiThread(() -> {
            UnityPlayer.UnitySendMessage(gameObjectName, "ApplyAssistantResponseJson", responseJson);
            Log.i(TAG, "Delivered assistant response to " + gameObjectName);
        });
    }
}
