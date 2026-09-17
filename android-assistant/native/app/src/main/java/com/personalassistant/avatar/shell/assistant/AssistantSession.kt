package com.personalassistant.avatar.shell.assistant

import android.content.SharedPreferences
import android.util.Log
import com.personalassistant.avatar.shell.avatar.AvatarBridge
import com.personalassistant.avatar.shell.avatar.AvatarCharacter
import com.personalassistant.avatar.shell.avatar.AvatarEmotion
import com.personalassistant.avatar.shell.avatar.AvatarMode
import java.io.File
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

enum class Activity {
    READY,
    LISTENING,
    SENDING,
    THINKING,
    WAITING_FOR_APPROVAL,
    SPEAKING,
    DONE,
    FAILED,
    CANCELLED,
}

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
    val pendingAction: PhoneAction? = null,
    val pendingApproval: PendingPullRequestApproval? = null,
    val approvalInFlight: Boolean = false,
    val hasApprovalToken: Boolean = false,
) {
    val isBusy: Boolean
        get() = activity == Activity.SENDING ||
            activity == Activity.THINKING ||
            activity == Activity.SPEAKING ||
            pendingApproval != null ||
            approvalInFlight
}

/**
 * Turns a typed request into a backend task and mirrors its event stream onto the avatar:
 * planning and execution show as thinking, the ASSISTANT_REPLY event is spoken with lip-sync.
 */
class AssistantSession(
    private val scope: CoroutineScope,
    private val avatar: AvatarBridge,
    private val preferences: SharedPreferences,
    private val speechCacheDirectory: File,
    private val approvalTokens: ApprovalTokenStore,
    private val runAction: (PhoneAction) -> String?,
) {
    private val state = MutableStateFlow(
        UiState(
            backendUrl = preferences.getString(KEY_BACKEND_URL, DEFAULT_BACKEND_URL) ?: DEFAULT_BACKEND_URL,
            character = AvatarCharacter.entries.firstOrNull {
                it.name == preferences.getString(KEY_CHARACTER, AvatarCharacter.COOL_MAN.name)
            } ?: AvatarCharacter.COOL_MAN,
            hasApprovalToken = approvalTokens.hasToken(),
        ),
    )
    val uiState: StateFlow<UiState> = state.asStateFlow()

    private var api = AssistantApi(state.value.backendUrl, speechCacheDirectory)

    /** One conversation per app launch, so follow-up questions keep their context. */
    private val conversationId = "android-${UUID.randomUUID()}"
    private var taskJob: Job? = null
    private var pendingOutcome: String? = null

    init {
        checkConnection()
        restoreActiveTask()
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
        api = AssistantApi(normalized, speechCacheDirectory)
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
            it.copy(
                activity = Activity.SENDING,
                lastRequest = request,
                reply = null,
                taskId = null,
                transcript = null,
                hint = null,
                pendingAction = null,
                pendingApproval = null,
                approvalInFlight = false,
            )
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
            preferences.edit()
                .putString(KEY_ACTIVE_TASK_ID, taskId)
                .putInt(KEY_ACTIVE_TASK_SEQUENCE, 0)
                .apply()
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
        if (state.value.pendingAction != null && outcome == "completed") {
            // Wait for the user to confirm or dismiss the proposed action.
            state.update { it.copy(activity = Activity.DONE) }
            avatar.command(AvatarMode.IDLE, AvatarEmotion.CURIOUS, 0.5f)
            return
        }
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

    /** Runs the confirmed action through the phone's apps and records the outcome on the task. */
    fun confirmAction() {
        val action = state.value.pendingAction ?: return
        val taskId = state.value.taskId
        state.update { it.copy(pendingAction = null) }
        val failure = runAction(action)
        Log.i(TAG, "NATIVE_ACTION_${if (failure == null) "DONE" else "FAILED"}: ${action.javaClass.simpleName}")
        pendingOutcome = if (failure == null) "completed" else "failed"
        val speech = failure ?: action.doneSpeech
        state.update { it.copy(reply = speech, activity = Activity.THINKING) }
        avatar.speak("action:${System.currentTimeMillis()}", speech, if (failure == null) AvatarEmotion.WARM else AvatarEmotion.CONCERNED, 0.65f)
        if (taskId != null) {
            scope.launch {
                runCatching {
                    api.addMessage(taskId, if (failure == null) "Phone action confirmed and started: ${action.summary}" else "Phone action failed: $failure")
                }
            }
        }
    }

    fun dismissAction() {
        val action = state.value.pendingAction ?: return
        val taskId = state.value.taskId
        state.update { it.copy(pendingAction = null, reply = "Okay, I won't do that.") }
        avatar.command(AvatarMode.IDLE, AvatarEmotion.NEUTRAL, 0.45f)
        Log.i(TAG, "NATIVE_ACTION_DISMISSED: ${action.javaClass.simpleName}")
        if (taskId != null) {
            scope.launch { runCatching { api.addMessage(taskId, "Phone action dismissed: ${action.summary}") } }
        }
    }

    /** Saves a runtime-provisioned secret encrypted by Android Keystore, never in the APK. */
    fun updateApprovalToken(token: String) {
        if (token.isBlank()) {
            approvalTokens.clear()
            state.update {
                it.copy(hasApprovalToken = false, hint = "Approval token removed from this device.")
            }
            return
        }
        runCatching { approvalTokens.save(token) }
            .onSuccess {
                state.update {
                    it.copy(hasApprovalToken = true, hint = "Approval token stored securely.")
                }
            }
            .onFailure { error ->
                state.update { it.copy(hint = error.message ?: "Could not store approval token.") }
            }
    }

    fun approvePullRequest() = decidePullRequest(approve = true)

    fun rejectPullRequest() = decidePullRequest(approve = false)

    private fun decidePullRequest(approve: Boolean) {
        val approval = state.value.pendingApproval ?: return
        val taskId = state.value.taskId ?: return
        val token = approvalTokens.load()
        if (token.isNullOrBlank()) {
            state.update {
                it.copy(hasApprovalToken = false, hint = "Add the approval token in Settings first.")
            }
            return
        }
        if (state.value.approvalInFlight) return
        state.update {
            it.copy(
                approvalInFlight = true,
                hint = if (approve) "Checking the reviewed commit with GitHub…" else "Rejecting merge…",
            )
        }
        scope.launch {
            try {
                api.decidePullRequestApproval(
                    taskId = taskId,
                    approvalToken = token,
                    approve = approve,
                    expectedHeadSha = approval.expectedHeadSha,
                )
                state.update {
                    it.copy(
                        activity = Activity.THINKING,
                        pendingApproval = null,
                        approvalInFlight = false,
                        hint = if (approve) "Merge approved. Confirming the result…" else "Merge rejected.",
                    )
                }
                avatar.command(AvatarMode.THINKING, AvatarEmotion.NEUTRAL, 0.5f)
            } catch (error: AssistantApiException) {
                Log.w(TAG, "NATIVE_APPROVAL_FAILED: ${error.message}")
                state.update {
                    it.copy(
                        activity = Activity.WAITING_FOR_APPROVAL,
                        approvalInFlight = false,
                        hint = error.message ?: "Approval failed. Review the pull request and try again.",
                    )
                }
                avatar.command(AvatarMode.IDLE, AvatarEmotion.CONCERNED, 0.5f)
            }
        }
    }

    /** Restores a task and its approval card after activity or process recreation. */
    private fun restoreActiveTask() {
        val taskId = preferences.getString(KEY_ACTIVE_TASK_ID, null) ?: return
        if (taskJob?.isActive == true) return
        state.update { it.copy(activity = Activity.THINKING, taskId = taskId) }
        taskJob = scope.launch {
            runCatching { api.pendingApproval(taskId) }
                .onSuccess { approval ->
                    if (approval != null) showPendingApproval(approval)
                }
                .onFailure { error ->
                    Log.i(TAG, "NATIVE_APPROVAL_RESTORE_DEFERRED: ${error.message}")
                }
            followEvents(taskId)
        }
    }

    private fun showPendingApproval(approval: PendingPullRequestApproval) {
        state.update {
            it.copy(
                activity = Activity.WAITING_FOR_APPROVAL,
                reply = "Pull request #${approval.number} is waiting for your review.",
                pendingApproval = approval,
                approvalInFlight = false,
                hint = if (approval.draft) {
                    "Open GitHub, review the changes, and mark the PR ready before approving."
                } else {
                    "Review the exact commit before approving."
                },
            )
        }
        avatar.command(AvatarMode.IDLE, AvatarEmotion.CURIOUS, 0.5f)
    }

    private fun clearActiveTask() {
        preferences.edit()
            .remove(KEY_ACTIVE_TASK_ID)
            .remove(KEY_ACTIVE_TASK_SEQUENCE)
            .apply()
    }

    private suspend fun followEvents(taskId: String) {
        var lastSequence = preferences.getInt(KEY_ACTIVE_TASK_SEQUENCE, 0)
        val deadline = System.currentTimeMillis() + TASK_TIMEOUT_MS
        var consecutiveErrors = 0
        var approvalObserved = state.value.pendingApproval != null
        while (
            System.currentTimeMillis() < deadline ||
            approvalObserved
        ) {
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
                if (event.type == "APPROVAL_REQUESTED") approvalObserved = true
                val terminal = handleEvent(taskId, event)
                preferences.edit().putInt(KEY_ACTIVE_TASK_SEQUENCE, lastSequence).apply()
                if (terminal) {
                    clearActiveTask()
                    return
                }
            }
            delay(POLL_INTERVAL_MS)
        }
        Log.w(TAG, "NATIVE_TASK_TIMEOUT: id=$taskId")
        failLocally("That is taking longer than expected. Please try again later.")
    }

    /** Returns true when the task reached a terminal event. */
    private suspend fun handleEvent(taskId: String, event: TaskEvent): Boolean {
        Log.i(TAG, "NATIVE_EVENT: task=$taskId seq=${event.sequence} type=${event.type}")
        when (event.type) {
            "TASK_PLANNING_STARTED", "PLAN_CREATED" -> avatar.command(AvatarMode.THINKING, AvatarEmotion.CURIOUS, 0.6f)
            "EXECUTION_STARTED" -> avatar.command(AvatarMode.THINKING, AvatarEmotion.NEUTRAL, 0.55f)
            "APPROVAL_REQUESTED" -> {
                val approval = PendingPullRequestApproval.fromJson(event.payload)
                if (approval == null) {
                    Log.w(TAG, "NATIVE_APPROVAL_INVALID: task=$taskId seq=${event.sequence}")
                    return false
                }
                showPendingApproval(approval)
            }
            "APPROVAL_GRANTED", "TOOL_CALLED" -> {
                state.update {
                    it.copy(
                        activity = Activity.THINKING,
                        approvalInFlight = true,
                    )
                }
                avatar.command(AvatarMode.THINKING, AvatarEmotion.NEUTRAL, 0.5f)
            }
            "TOOL_RESULT_RECEIVED" -> {
                if (
                    state.value.pendingApproval != null &&
                    event.payload.optString("tool") == "github"
                ) {
                    val succeeded = event.payload.optBoolean("ok", false)
                    state.update {
                        it.copy(
                            activity = if (succeeded) {
                                Activity.THINKING
                            } else {
                                Activity.WAITING_FOR_APPROVAL
                            },
                            approvalInFlight = succeeded,
                        )
                    }
                }
            }
            "ASSISTANT_REPLY" -> {
                val text = event.payload.optString("text")
                pendingOutcome = event.payload.optString("outcome", "completed")
                val action = PhoneAction.fromJson(event.payload.optJSONObject("action"))
                state.update {
                    it.copy(
                        reply = text,
                        pendingAction = action,
                        pendingApproval = null,
                        approvalInFlight = false,
                    )
                }
                if (action != null) Log.i(TAG, "NATIVE_ACTION_PROPOSED: ${action.summary}")
                if (text.isNotBlank()) {
                    val audioPath = try {
                        api.speech(text, event.payload.optString("emotion", "Warm")).also {
                            Log.i(TAG, "NATIVE_CLOUD_TTS_READY: task=$taskId")
                        }
                    } catch (error: AssistantApiException) {
                        Log.i(TAG, "NATIVE_CLOUD_TTS_FALLBACK: ${error.message}")
                        null
                    }
                    avatar.speak(
                        responseId = "task:$taskId:${event.sequence}",
                        text = text,
                        emotion = AvatarEmotion.fromName(event.payload.optString("emotion")),
                        intensity = event.payload.optDouble("intensity", 0.65).toFloat(),
                        audioPath = audioPath,
                    )
                    Log.i(TAG, "NATIVE_REPLY_DELIVERED: task=$taskId outcome=$pendingOutcome chars=${text.length}")
                }
            }
            "TASK_COMPLETED", "TASK_FAILED", "TASK_CANCELLED" -> {
                state.update {
                    it.copy(pendingApproval = null, approvalInFlight = false)
                }
                return true
            }
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
        private const val KEY_ACTIVE_TASK_ID = "active_task_id"
        private const val KEY_ACTIVE_TASK_SEQUENCE = "active_task_sequence"
        private const val POLL_INTERVAL_MS = 500L
        private const val TASK_TIMEOUT_MS = 90_000L
        private const val MAX_REQUEST_CHARACTERS = 2_000
    }
}
