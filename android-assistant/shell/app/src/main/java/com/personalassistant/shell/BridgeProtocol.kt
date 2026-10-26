package com.personalassistant.shell

import org.json.JSONObject

/**
 * The messages the console page and the app exchange, as JSON strings.
 *
 * The page can ask for voice and speech, and confirm an action a server reply proposed.
 * It can never describe an action itself: the page names the reply's card, the app reads
 * the action from the server and checks it, and the page then refers to it only by the id
 * the app gave it.
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

    fun capabilities(voice: Boolean, assistant: Boolean? = null): String = JSONObject()
        .put("type", "capabilities")
        .put("voice", voice)
        .apply { if (assistant != null) put("assistant", assistant) }
        .put("speak", true)
        // The page may send a reply a sentence at a time ("speak_more" queues it).
        .put("speakQueue", true)
        // Speech start, each spoken word and done arrive as "speech" messages (lip sync).
        .put("speechEvents", true)
        // Reminders and alarms ring from the app; the page asks it to sync after a reply.
        .put("alarms", true)
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

    /** Speech as it happens: "start", "word" (with the word being spoken) or "done". */
    fun speech(state: String, word: String? = null): String = JSONObject()
        .put("type", "speech")
        .put("state", state)
        .apply { if (word != null) put("word", word.take(80)) }
        .toString()

    fun voice(state: String, text: String? = null): String = JSONObject()
        .put("type", "voice")
        .put("state", state)
        .apply { if (text != null) put("text", text) }
        .toString()

    /** A reply's phone-action card, checked by the app: its label and id, or why not. */
    fun messageAction(blockId: String, actionId: String?, label: String?, error: String?): String =
        JSONObject()
            .put("type", "message_action")
            .put("id", blockId)
            .apply {
                if (error != null) put("error", error) else put("action", actionId).put("label", label)
            }
            .toString()

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
        "speak_more",
        "stop_speaking",
        "run_action",
        "offer_message_action",
        "sync_alarms",
    )
}
