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

    /** Most a shared file or screen may carry to the page (the server caps it again). */
    const val MAX_SHARED_TEXT = 5_000_000
    const val MAX_SCREEN_TEXT = 8_000

    fun capabilities(voice: Boolean, deviceAi: DeviceAiState, assistant: Boolean? = null): String =
        JSONObject()
        .put("type", "capabilities")
        .put("voice", voice)
        .apply { if (assistant != null) put("assistant", assistant) }
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

    /** Text shared from another app, for the chat box. */
    fun shared(text: String): String = JSONObject()
        .put("type", "shared")
        .put("text", text.take(MAX_TEXT))
        .toString()

    /** A text file shared from another app, to upload into the chat. */
    fun sharedFile(name: String, text: String): String = JSONObject()
        .put("type", "shared_file")
        .put("name", name.take(200))
        .put("text", text.take(MAX_SHARED_TEXT))
        .toString()

    /** The screen the user was on when they opened the assistant (may be empty). */
    fun screen(app: String, text: String): String = JSONObject()
        .put("type", "screen")
        .put("app", app.take(80))
        .put("text", text.take(MAX_SCREEN_TEXT))
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
        "open_assistant_settings",
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
