package com.personalassistant.shell

import org.json.JSONObject

/**
 * The messages the console page and the app exchange, as JSON strings.
 *
 * The page can ask for voice, speech and on-device routing, and confirm an action the app
 * proposed. It can never describe an action itself: the app keeps the proposed actions and
 * the page only refers to one by the id the app gave it.
 */
object BridgeProtocol {
    const val MAX_TEXT = 4_000

    data class Incoming(val type: String, val id: String?, val text: String?)

    fun parse(raw: String): Incoming? {
        val payload = runCatching { JSONObject(raw) }.getOrNull() ?: return null
        val type = payload.optString("type").takeIf { it in INCOMING_TYPES } ?: return null
        return Incoming(
            type = type,
            id = payload.optString("id").takeIf { it.isNotBlank() && it.length <= 64 },
            text = payload.optString("text").takeIf { it.isNotBlank() }?.take(MAX_TEXT),
        )
    }

    fun capabilities(voice: Boolean, deviceAi: DeviceAiState): String = JSONObject()
        .put("type", "capabilities")
        .put("voice", voice)
        .put("speak", true)
        .put("onDevice", deviceAi.availability.wire)
        .put("onDeviceDetail", deviceAi.detail)
        .toString()

    fun deviceAi(state: DeviceAiState): String = JSONObject()
        .put("type", "device_ai")
        .put("onDevice", state.availability.wire)
        .put("onDeviceDetail", state.detail)
        .apply { state.progress?.let { put("progress", it.toDouble()) } }
        .toString()

    fun voice(state: String, text: String? = null): String = JSONObject()
        .put("type", "voice")
        .put("state", state)
        .apply { if (text != null) put("text", text) }
        .toString()

    fun route(id: String, route: RequestRoute, actionId: String? = null): String {
        val message = JSONObject().put("type", "route").put("id", id)
        when (route) {
            is RequestRoute.Reply -> message.put("route", "reply").put("text", route.text)
            is RequestRoute.Confirm -> message
                .put("route", "confirm")
                .put("label", route.action.label)
                .put("action", requireNotNull(actionId))
            is RequestRoute.Cloud -> message.put("route", "cloud")
        }
        return message.toString()
    }

    fun actionResult(actionId: String, error: String?): String = JSONObject()
        .put("type", "action_result")
        .put("action", actionId)
        .apply { if (error != null) put("error", error) }
        .toString()

    private val INCOMING_TYPES = setOf(
        "hello",
        "listen",
        "stop_listening",
        "speak",
        "stop_speaking",
        "route",
        "run_action",
        "enable_on_device",
    )
}
