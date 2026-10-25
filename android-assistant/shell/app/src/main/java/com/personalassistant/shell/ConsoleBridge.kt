package com.personalassistant.shell

import android.net.Uri
import android.webkit.WebView
import androidx.webkit.JavaScriptReplyProxy
import androidx.webkit.WebMessageCompat
import androidx.webkit.WebViewCompat
import java.time.ZoneId
import java.util.UUID
import org.json.JSONObject
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/**
 * What the console page can ask of the phone: listen, speak, and run the actions a server
 * reply proposed once the user confirms them. The phone never decides what a message
 * means; every message goes to the server. Registered only for the console's own origin,
 * so no other page (or frame) ever sees it. Everything runs on the main thread.
 */
class ConsoleBridge(
    private val scope: CoroutineScope,
    private val speech: SpeechOutput,
    private val actions: PhoneActionRunner,
    private val voiceAvailable: () -> Boolean,
    private val startListening: () -> Unit,
    private val stopListening: () -> Unit,
    private val isAssistant: () -> Boolean? = { null },
    private val openAssistantSettings: () -> Unit = {},
    /** Reads a reply's phone-action card from the console's server (see ServerActions). */
    private val fetchServerAction: suspend (path: String) -> JSONObject? = { null },
) : WebViewCompat.WebMessageListener, VoiceInput.Listener {
    private var page: JavaScriptReplyProxy? = null
    /** Shared or screen context that arrived before the page said hello (cold start). */
    private var pending: String? = null
    private val proposedActions = mutableMapOf<String, LocalAction>()

    override fun onPostMessage(
        view: WebView,
        message: WebMessageCompat,
        sourceOrigin: Uri,
        isMainFrame: Boolean,
        replyProxy: JavaScriptReplyProxy,
    ) {
        if (!isMainFrame) return
        page = replyProxy
        val incoming = BridgeProtocol.parse(message.data ?: return) ?: return
        when (incoming.type) {
            "hello" -> {
                send(BridgeProtocol.capabilities(voiceAvailable(), isAssistant()))
                pending?.let(::send)
                pending = null
            }
            "open_assistant_settings" -> openAssistantSettings()
            "listen" -> startListening()
            "stop_listening" -> stopListening()
            "speak" -> incoming.text?.let { speech.speak(it) {} }
            "stop_speaking" -> speech.stop()
            "run_action" -> runAction(incoming.id)
            "offer_message_action" -> offerServerAction(incoming.id, incoming.text)
        }
    }

    /**
     * A phone action a server reply proposed. Only the card's ids come from the page; the
     * action itself is read from the server and checked here before it can be confirmed.
     */
    private fun offerServerAction(blockId: String?, reference: String?) {
        val id = blockId ?: return
        val path = reference?.let { ServerActions.path(it, id) }
        if (path == null) {
            send(BridgeProtocol.messageAction(id, null, null, "That action can't be read. Ask again."))
            return
        }
        scope.launch {
            val card = fetchServerAction(path)
            val action = card?.takeIf { it.optString("id") == id }?.optJSONObject("action")
                ?.let { ServerActions.toLocal(it, ZoneId.systemDefault()) }
            if (action == null) {
                send(
                    BridgeProtocol.messageAction(
                        id, null, null, "This phone can't do that action. Ask again with the details.",
                    ),
                )
                return@launch
            }
            val key = UUID.randomUUID().toString().also { proposedActions[it] = action }
            send(BridgeProtocol.messageAction(id, key, action.label, null))
        }
    }

    /** Runs an action the app checked and the user confirmed on the page. */
    private fun runAction(actionId: String?) {
        val action = actionId?.let(proposedActions::remove)
        if (action == null) {
            send(BridgeProtocol.actionResult(actionId.orEmpty(), "That action expired. Ask again."))
            return
        }
        send(BridgeProtocol.actionResult(actionId, actions.run(action)))
    }

    fun send(message: String) {
        page?.postMessage(message)
    }

    /** Context from another app: now if the page is listening, else once it says hello. */
    fun deliver(message: String) {
        val current = page
        if (current == null) pending = message else current.postMessage(message)
    }

    override fun onListening() = send(BridgeProtocol.voice("listening"))
    override fun onPartialTranscript(text: String) = send(BridgeProtocol.voice("partial", text))
    override fun onFinalTranscript(text: String) = send(BridgeProtocol.voice("final", text))
    override fun onVoiceLevel(level: Float) = Unit
    override fun onError(message: String) = send(BridgeProtocol.voice("error", message))

    fun close() {
        speech.close()
    }
}
