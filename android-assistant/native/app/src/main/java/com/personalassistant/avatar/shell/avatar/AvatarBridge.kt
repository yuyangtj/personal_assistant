package com.personalassistant.avatar.shell.avatar

import com.personalassistant.avatar.MiloAssistantBridge
import com.unity3d.player.UnityPlayer
import org.json.JSONObject

enum class AvatarMode(val unityName: String) {
    IDLE("Idle"), LISTENING("Listening"), THINKING("Thinking"), SPEAKING("Speaking"), SUCCESS("Success"), ERROR("Error")
}

enum class AvatarEmotion(val unityName: String) {
    NEUTRAL("Neutral"), WARM("Warm"), CURIOUS("Curious"), EXCITED("Excited"), CONCERNED("Concerned");

    companion object {
        fun fromName(value: String?): AvatarEmotion =
            entries.firstOrNull { it.unityName.equals(value, ignoreCase = true) } ?: WARM
    }
}

enum class AvatarCharacter(val unityName: String, val label: String, val credit: String?) {
    COOL_MAN(
        "CoolMan",
        "Cool Man",
        "\"Cool Man\" by ardhanaputra · CC BY 4.0 · modified",
    ),
    MILO("Milo", "Milo", null),
}

/** High-level commands for the Unity avatar; Unity owns animation, visemes and timing. */
class AvatarBridge {
    fun command(mode: AvatarMode, emotion: AvatarEmotion, intensity: Float = 0.6f) {
        val json = JSONObject()
            .put("mode", mode.unityName)
            .put("emotion", emotion.unityName)
            .put("intensity", intensity.coerceIn(0f, 1f).toDouble())
        send("ApplyCommandJson", json.toString())
    }

    /** Speaks [text] with lip-sync. A repeated [responseId] is ignored by Unity. */
    fun speak(responseId: String, text: String, emotion: AvatarEmotion, intensity: Float) {
        val json = JSONObject()
            .put("responseId", responseId)
            .put("text", text.take(MAX_SPEECH_CHARACTERS))
            .put("emotion", emotion.unityName)
            .put("intensity", intensity.coerceIn(0f, 1f).toDouble())
        MiloAssistantBridge.deliverResponse(TARGET, json.toString())
    }

    fun setCharacter(character: AvatarCharacter) {
        send("SetCharacterName", character.unityName)
    }

    private fun send(method: String, value: String) {
        UnityPlayer.UnitySendMessage(TARGET, method, value)
    }

    private companion object {
        const val TARGET = "AvatarRuntime"
        const val MAX_SPEECH_CHARACTERS = 4000
    }
}
