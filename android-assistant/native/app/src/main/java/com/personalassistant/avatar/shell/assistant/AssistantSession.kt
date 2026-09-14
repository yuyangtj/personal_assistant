package com.personalassistant.avatar.shell.assistant

import android.content.SharedPreferences
import android.util.Log
import com.personalassistant.avatar.shell.avatar.AvatarBridge
import com.personalassistant.avatar.shell.avatar.AvatarCharacter
import com.personalassistant.avatar.shell.avatar.AvatarEmotion
import com.personalassistant.avatar.shell.avatar.AvatarMode
import java.util.UUID
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

enum class Connection { CHECKING, ONLINE, OFFLINE }

enum class Activity { READY, LISTENING, SENDING, THINKING, SPEAKING, DONE, FAILED, CANCELLED }

data class UiState(
    val connection: Connection = Connection.CHECKING,
    val activity: Activity = Activity.READY,
    val lastRequest: String? = null,
    val reply: String? = null,
    val taskId: String? = null,
    val backendUrl: String = AssistantSession.DEFAULT_BACKEND_URL,
    val character: AvatarCharacter = AvatarCharacter.COOL_MAN,
    val transcript: String? = null,
    val voiceLevel: Float = 0f,
    val hint: String? = null,
) {
    val isBusy: Boolean get() = activity == Activity.SENDING || activity == Activity.THINKING || activity == Activity.SPEAKING
}

/**
 * Turns a typed request into a backend task and mirrors its event stream onto the avatar:
 * planning and execution show as thinking, the ASSISTANT_REPLY event is spoken with lip-sync.
 */
class AssistantSession(
    private val scope: CoroutineScope,
    private val avatar: AvatarBridge,
    private val preferences: SharedPreferences,
) {
    private val state = MutableStateFlow(
        UiState(
            backendUrl = preferences.getString(KEY_BACKEND_URL, DEFAULT_BACKEND_URL) ?: DEFAULT_BACKEND_URL,
            character = AvatarCharacter.entries.firstOrNull {
                it.name == preferences.getString(KEY_CHARACTER, AvatarCharacter.COOL_MAN.name)
            } ?: AvatarCharacter.COOL_MAN,
        ),
    )
    val uiState: StateFlow<UiState> = state.asStateFlow()

    private var api = AssistantApi(state.value.backendUrl)

    /** One conversation per app launch, so follow-up questions keep their context. */
    private val conversationId = "android-${UUID.randomUUID()}"
    private var taskJob: Job? = null
    private var pendingOutcome: String? = null

    init {
        checkConnection()
    }

    fun checkConnection() {
        scope.launch {
            state.update { it.copy(connection = Connection.CHECKING) }
            val online = api.health()
            state.update { it.copy(connection = if (online) Connection.ONLINE else Connection.OFFLINE) }
            Log.i(TAG, "NATIVE_HEALTH: ${if (online) "online" else "offline"} url=${state.value.backendUrl}")
        }
    }

    fun updateBackendUrl(url: String) {
        val normalized = url.trim().trimEnd('/')
        if (normalized.isEmpty()) return
        preferences.edit().putString(KEY_BACKEND_URL, normalized).apply()
        api = AssistantApi(normalized)
        state.update { it.copy(backendUrl = normalized) }
        checkConnection()
    }

    fun selectCharacter(character: AvatarCharacter) {
        preferences.edit().putString(KEY_CHARACTER, character.name).apply()
        state.update { it.copy(character = character) }
        avatar.setCharacter(character)
    }

    fun send(prompt: String) {
        val request = prompt.trim().take(MAX_REQUEST_CHARACTERS)
        if (request.isEmpty() || state.value.isBusy) return
        pendingOutcome = null
        state.update {
            it.copy(activity = Activity.SENDING, lastRequest = request, reply = null, taskId = null, transcript = null, hint = null)
        }
        avatar.command(AvatarMode.THINKING, AvatarEmotion.CURIOUS, 0.6f)

        taskJob = scope.launch {
            val taskId = try {
                api.createTask(request, UUID.randomUUID().toString(), conversationId)
            } catch (error: AssistantApiException) {
                Log.w(TAG, "NATIVE_TASK_CREATE_FAILED: ${error.message}")
                failLocally("I can't reach the assistant server right now. Please check the connection.")
                return@launch
            }
            Log.i(TAG, "NATIVE_TASK_CREATED: id=$taskId chars=${request.length}")
            state.update { it.copy(activity = Activity.THINKING, taskId = taskId, connection = Connection.ONLINE) }
            followEvents(taskId)
        }
    }

    fun cancel() {
        val taskId = state.value.taskId ?: return
        if (state.value.activity != Activity.THINKING && state.value.activity != Activity.SENDING) return
        scope.launch {
            try {
                api.cancel(taskId)
                Log.i(TAG, "NATIVE_TASK_CANCEL_REQUESTED: id=$taskId")
            } catch (error: AssistantApiException) {
                Log.w(TAG, "NATIVE_TASK_CANCEL_FAILED: ${error.message}")
            }
        }
    }

    /** The microphone is open: the avatar listens and the transcript updates live. */
    fun onListening() {
        if (state.value.isBusy) return
        state.update { it.copy(activity = Activity.LISTENING, transcript = "", voiceLevel = 0f, hint = null) }
        avatar.command(AvatarMode.LISTENING, AvatarEmotion.CURIOUS, 0.6f)
        Log.i(TAG, "NATIVE_LISTENING")
    }

    fun onPartialTranscript(text: String) {
        if (state.value.activity == Activity.LISTENING) state.update { it.copy(transcript = text) }
    }

    fun onVoiceLevel(level: Float) {
        if (state.value.activity == Activity.LISTENING) state.update { it.copy(voiceLevel = level) }
    }

    /** A finished utterance is sent exactly like a typed request. */
    fun onFinalTranscript(text: String) {
        Log.i(TAG, "NATIVE_VOICE_REQUEST: chars=${text.length}")
        state.update { it.copy(activity = Activity.READY, transcript = null, voiceLevel = 0f) }
        send(text)
    }

    fun onVoiceError(message: String) {
        state.update {
            it.copy(
                activity = if (it.activity == Activity.LISTENING) Activity.READY else it.activity,
                transcript = null,
                voiceLevel = 0f,
                hint = message,
            )
        }
        if (!state.value.isBusy) avatar.command(AvatarMode.IDLE, AvatarEmotion.WARM, 0.45f)
    }

    /** Called by the Unity host bridge when the avatar starts speaking a response. */
    fun onSpeechStarted(responseId: String) {
        if (responseId.isNotEmpty()) state.update { it.copy(activity = Activity.SPEAKING) }
    }

    /** Called by the Unity host bridge when speech ends; shows the task outcome on the avatar. */
    fun onSpeechFinished(responseId: String, reason: String) {
        if (state.value.activity != Activity.SPEAKING && state.value.activity != Activity.THINKING) return
        val outcome = pendingOutcome ?: return
        Log.i(TAG, "NATIVE_SPEECH_FINISHED: id=$responseId reason=$reason outcome=$outcome")
        val (activity, mode, emotion) = when (outcome) {
            "completed" -> Triple(Activity.DONE, AvatarMode.SUCCESS, AvatarEmotion.WARM)
            "cancelled" -> Triple(Activity.CANCELLED, AvatarMode.IDLE, AvatarEmotion.NEUTRAL)
            else -> Triple(Activity.FAILED, AvatarMode.ERROR, AvatarEmotion.CONCERNED)
        }
        state.update { it.copy(activity = activity) }
        avatar.command(mode, emotion, 0.6f)
        scope.launch {
            delay(if (mode == AvatarMode.SUCCESS) 4_500 else 2_500)
            if (!state.value.isBusy) avatar.command(AvatarMode.IDLE, AvatarEmotion.WARM, 0.45f)
        }
    }

    private suspend fun followEvents(taskId: String) {
        var lastSequence = 0
        val deadline = System.currentTimeMillis() + TASK_TIMEOUT_MS
        var consecutiveErrors = 0
        while (System.currentTimeMillis() < deadline) {
            val events = try {
                api.events(taskId).also { consecutiveErrors = 0 }
            } catch (error: AssistantApiException) {
                if (++consecutiveErrors >= 6) {
                    Log.w(TAG, "NATIVE_EVENTS_FAILED: ${error.message}")
                    failLocally("I lost the connection to the assistant server.")
                    return
                }
                delay(POLL_INTERVAL_MS)
                continue
            }
            for (event in events.filter { it.sequence > lastSequence }.sortedBy { it.sequence }) {
                lastSequence = event.sequence
                if (handleEvent(taskId, event)) return
            }
            delay(POLL_INTERVAL_MS)
        }
        Log.w(TAG, "NATIVE_TASK_TIMEOUT: id=$taskId")
        failLocally("That is taking longer than expected. Please try again later.")
    }

    /** Returns true when the task reached a terminal event. */
    private fun handleEvent(taskId: String, event: TaskEvent): Boolean {
        Log.i(TAG, "NATIVE_EVENT: task=$taskId seq=${event.sequence} type=${event.type}")
        when (event.type) {
            "TASK_PLANNING_STARTED", "PLAN_CREATED" -> avatar.command(AvatarMode.THINKING, AvatarEmotion.CURIOUS, 0.6f)
            "EXECUTION_STARTED" -> avatar.command(AvatarMode.THINKING, AvatarEmotion.NEUTRAL, 0.55f)
            "ASSISTANT_REPLY" -> {
                val text = event.payload.optString("text")
                pendingOutcome = event.payload.optString("outcome", "completed")
                state.update { it.copy(reply = text) }
                if (text.isNotBlank()) {
                    avatar.speak(
                        responseId = "task:$taskId:${event.sequence}",
                        text = text,
                        emotion = AvatarEmotion.fromName(event.payload.optString("emotion")),
                        intensity = event.payload.optDouble("intensity", 0.65).toFloat(),
                    )
                    Log.i(TAG, "NATIVE_REPLY_DELIVERED: task=$taskId outcome=$pendingOutcome chars=${text.length}")
                }
            }
            "TASK_COMPLETED", "TASK_FAILED", "TASK_CANCELLED" -> return true
        }
        return false
    }

    private fun failLocally(message: String) {
        pendingOutcome = "failed"
        state.update { it.copy(activity = Activity.THINKING, reply = message, connection = Connection.OFFLINE) }
        avatar.speak("local:${System.currentTimeMillis()}", message, AvatarEmotion.CONCERNED, 0.6f)
    }

    companion object {
        const val DEFAULT_BACKEND_URL = "http://127.0.0.1:8010"
        private const val TAG = "MiloNative"
        private const val KEY_BACKEND_URL = "backend_url"
        private const val KEY_CHARACTER = "character"
        private const val POLL_INTERVAL_MS = 500L
        private const val TASK_TIMEOUT_MS = 90_000L
        private const val MAX_REQUEST_CHARACTERS = 2_000
    }
}
