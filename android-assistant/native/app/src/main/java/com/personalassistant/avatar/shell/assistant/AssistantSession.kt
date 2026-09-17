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

data class TaskCenterState(
    val tasks: List<AssistantTask> = emptyList(),
    val loading: Boolean = false,
    val error: String? = null,
    val selectedTask: AssistantTask? = null,
    val selectedEvents: List<TaskEvent> = emptyList(),
    val selectedApproval: PendingPullRequestApproval? = null,
    val detailLoading: Boolean = false,
    val actionInFlight: Boolean = false,
)

data class ChatCenterState(
    val sessions: List<ChatSession> = emptyList(),
    val messages: List<ChatMessage> = emptyList(),
    val tasks: List<AssistantTask> = emptyList(),
    val loading: Boolean = false,
    val sending: Boolean = false,
    val error: String? = null,
    /** Work the backend suggests for [proposalMessageId], awaiting the user's confirmation. */
    val proposal: TaskProposal? = null,
    val proposalMessageId: String? = null,
)

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
    val taskCenter: TaskCenterState = TaskCenterState(),
    val chatSession: ChatSession? = null,
    val chatCenter: ChatCenterState = ChatCenterState(),
) {
    val isBusy: Boolean
        get() = activity == Activity.SENDING ||
            activity == Activity.THINKING ||
            activity == Activity.SPEAKING ||
            pendingApproval != null ||
            approvalInFlight ||
            chatCenter.loading
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

    private var taskJob: Job? = null
    private var pendingOutcome: String? = null

    @Volatile
    private var chatRefreshInFlight = false

    init {
        checkConnection()
        refreshChatSessions()
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

    fun refreshChatSessions() {
        if (state.value.chatCenter.loading) return
        state.update {
            it.copy(chatCenter = it.chatCenter.copy(loading = true, error = null))
        }
        scope.launch {
            try {
                val sessions = api.chatSessions()
                val preferredId = state.value.chatSession?.id
                    ?: preferences.getString(KEY_CHAT_SESSION_ID, null)
                val selected = sessions.firstOrNull { it.id == preferredId }
                if (preferredId != null && selected == null) {
                    preferences.edit().remove(KEY_CHAT_SESSION_ID).apply()
                }
                state.update {
                    it.copy(
                        connection = Connection.ONLINE,
                        chatSession = selected,
                        chatCenter = it.chatCenter.copy(
                            sessions = sessions,
                            loading = false,
                            error = null,
                        ),
                    )
                }
            } catch (error: AssistantApiException) {
                state.update {
                    it.copy(
                        chatCenter = it.chatCenter.copy(
                            loading = false,
                            error = error.message ?: "Could not load conversations.",
                        ),
                    )
                }
            }
        }
    }

    fun startNewChat() {
        if (state.value.isBusy || state.value.chatCenter.loading) return
        state.update {
            it.copy(chatCenter = it.chatCenter.copy(loading = true, error = null))
        }
        scope.launch {
            try {
                val chat = api.createChatSession()
                activateChat(chat)
                state.update {
                    it.copy(
                        activity = Activity.READY,
                        lastRequest = null,
                        reply = null,
                        taskId = null,
                        hint = "New conversation started.",
                        pendingAction = null,
                        pendingApproval = null,
                        chatCenter = it.chatCenter.copy(
                            sessions = listOf(chat) + it.chatCenter.sessions.filterNot { item -> item.id == chat.id },
                            messages = emptyList(),
                            tasks = emptyList(),
                            loading = false,
                        ),
                    )
                }
                avatar.command(AvatarMode.IDLE, AvatarEmotion.WARM, 0.45f)
            } catch (error: AssistantApiException) {
                state.update {
                    it.copy(
                        chatCenter = it.chatCenter.copy(
                            loading = false,
                            error = error.message ?: "Could not start a conversation.",
                        ),
                    )
                }
            }
        }
    }

    fun resumeChat(chatSessionId: String) {
        if (state.value.isBusy) return
        val chat = state.value.chatCenter.sessions.firstOrNull { it.id == chatSessionId } ?: return
        activateChat(chat)
        state.update {
            it.copy(
                activity = Activity.READY,
                lastRequest = null,
                reply = null,
                taskId = null,
                hint = "Conversation resumed.",
                pendingAction = null,
                pendingApproval = null,
                chatCenter = it.chatCenter.copy(loading = true, error = null),
            )
        }
        scope.launch {
            try {
                val tasks = api.chatSessionTasks(chat.id)
                val messages = api.chatMessages(chat.id)
                val latest = tasks.firstOrNull()
                val reply = messages.lastOrNull { it.role == "assistant" }?.content
                state.update { current ->
                    if (current.chatSession?.id != chat.id) return@update current
                    current.copy(
                        lastRequest = latest?.request,
                        reply = reply,
                        chatCenter = current.chatCenter.copy(
                            messages = messages,
                            tasks = tasks,
                            loading = false,
                            error = null,
                        ),
                    )
                }
            } catch (error: AssistantApiException) {
                state.update {
                    it.copy(chatCenter = it.chatCenter.copy(loading = false, error = error.message))
                }
            }
        }
    }

    fun discussTask(taskId: String) {
        if (state.value.chatCenter.loading) return
        state.update {
            it.copy(chatCenter = it.chatCenter.copy(loading = true, error = null))
        }
        scope.launch {
            try {
                val chat = api.ensureTaskChatSession(taskId)
                activateChat(chat)
                val tasks = api.chatSessionTasks(chat.id)
                val messages = api.chatMessages(chat.id)
                state.update {
                    it.copy(
                        activity = Activity.READY,
                        lastRequest = messages.lastOrNull { message -> message.role == "user" }?.content,
                        reply = messages.lastOrNull { message -> message.role == "assistant" }?.content,
                        taskId = null,
                        hint = "Task conversation opened.",
                        pendingAction = null,
                        pendingApproval = null,
                        chatCenter = it.chatCenter.copy(
                            sessions = listOf(chat) + it.chatCenter.sessions.filterNot { item -> item.id == chat.id },
                            messages = messages,
                            tasks = tasks,
                            loading = false,
                            error = null,
                        ),
                    )
                }
            } catch (error: AssistantApiException) {
                state.update {
                    it.copy(
                        chatCenter = it.chatCenter.copy(
                            loading = false,
                            error = error.message ?: "Could not open this task's conversation.",
                        ),
                    )
                }
            }
        }
    }

    /**
     * Records a turn of the conversation, then decides whether it may run on its own.
     *
     * Talking is free: an ordinary message goes straight to the assistant for a reply.
     * Work the backend flags as consequential — anything touching code or infrastructure —
     * stops here as a proposal until the user taps Create task.
     */
    fun sendChatMessage(prompt: String) {
        val content = prompt.trim().take(MAX_REQUEST_CHARACTERS)
        if (content.isEmpty() || state.value.chatCenter.sending || state.value.isBusy) return
        state.update {
            it.copy(
                chatCenter = it.chatCenter.copy(
                    sending = true,
                    error = null,
                    proposal = null,
                    proposalMessageId = null,
                ),
            )
        }
        scope.launch {
            val chat = try {
                ensureChatSession()
            } catch (error: AssistantApiException) {
                finishChatSendWithError("I can't start a conversation with the assistant server.")
                return@launch
            }
            val posted = try {
                api.postChatMessage(chat.id, content)
            } catch (error: AssistantApiException) {
                Log.w(TAG, "NATIVE_CHAT_MESSAGE_FAILED: ${error.message}")
                finishChatSendWithError("I couldn't save that message.")
                return@launch
            }
            state.update {
                if (it.chatSession?.id != chat.id) return@update it
                it.copy(
                    connection = Connection.ONLINE,
                    chatCenter = it.chatCenter.copy(
                        sending = false,
                        messages = it.chatCenter.messages + posted.message,
                    ),
                )
            }
            val proposal = posted.proposal
            if (proposal != null && proposal.consequential) {
                Log.i(TAG, "NATIVE_TASK_PROPOSED: chat=${chat.id} message=${posted.message.id}")
                state.update {
                    it.copy(
                        hint = "This would change code or infrastructure.",
                        chatCenter = it.chatCenter.copy(
                            proposal = proposal,
                            proposalMessageId = posted.message.id,
                        ),
                    )
                }
                return@launch
            }
            launchTaskFromMessage(chat, posted.message.id, proposal)
        }
    }

    /** The user confirmed the proposed task: this is the only path that starts costly work. */
    fun confirmTaskProposal() {
        val chat = state.value.chatSession ?: return
        val messageId = state.value.chatCenter.proposalMessageId ?: return
        val proposal = state.value.chatCenter.proposal
        state.update {
            it.copy(
                hint = null,
                chatCenter = it.chatCenter.copy(proposal = null, proposalMessageId = null),
            )
        }
        scope.launch { launchTaskFromMessage(chat, messageId, proposal) }
    }

    /** Declines the proposal. The message stays in the transcript as a note; nothing runs. */
    fun dismissTaskProposal() {
        if (state.value.chatCenter.proposal == null) return
        Log.i(TAG, "NATIVE_TASK_PROPOSAL_DISMISSED")
        state.update {
            it.copy(
                hint = "Kept as a note. Nothing was started.",
                chatCenter = it.chatCenter.copy(proposal = null, proposalMessageId = null),
            )
        }
    }

    /** "Create task" tapped on a message the user already sent, proposed or not. */
    fun createTaskFromChatMessage(messageId: String) {
        val chat = state.value.chatSession ?: return
        if (state.value.isBusy || state.value.chatCenter.sending) return
        state.update {
            it.copy(
                hint = null,
                chatCenter = it.chatCenter.copy(
                    sending = true,
                    error = null,
                    proposal = null,
                    proposalMessageId = null,
                ),
            )
        }
        scope.launch { launchTaskFromMessage(chat, messageId, null) }
    }

    /** Starts a new task continuing [taskId], carrying only the backend's curated context. */
    fun createFollowUpTask(taskId: String, prompt: String) {
        val content = prompt.trim().take(MAX_REQUEST_CHARACTERS)
        if (content.isEmpty() || state.value.isBusy) return
        state.update { it.copy(chatCenter = it.chatCenter.copy(sending = true, error = null)) }
        scope.launch {
            val task = try {
                api.createFollowUpTask(taskId, content)
            } catch (error: AssistantApiException) {
                Log.w(TAG, "NATIVE_FOLLOW_UP_FAILED: ${error.message}")
                finishChatSendWithError("I couldn't start a follow-up task.")
                return@launch
            }
            Log.i(TAG, "NATIVE_FOLLOW_UP_CREATED: id=${task.id} parent=$taskId")
            beginFollowingChatTask(task.id, content)
        }
    }

    private suspend fun launchTaskFromMessage(
        chat: ChatSession,
        messageId: String,
        proposal: TaskProposal?,
    ) {
        val task = try {
            api.createTaskFromMessage(
                chatSessionId = chat.id,
                messageId = messageId,
                goal = proposal?.suggestedGoal,
                requiredCapabilities = proposal?.requiredCapabilities.orEmpty(),
            )
        } catch (error: AssistantApiException) {
            Log.w(TAG, "NATIVE_TASK_FROM_MESSAGE_FAILED: ${error.message}")
            finishChatSendWithError("I can't reach the assistant server right now.")
            return
        }
        Log.i(TAG, "NATIVE_TASK_CREATED: id=${task.id} origin=$messageId")
        beginFollowingChatTask(task.id, task.request)
    }

    /** Mirrors a chat-launched task onto the avatar and the transcript's live task card. */
    private suspend fun beginFollowingChatTask(taskId: String, request: String) {
        pendingOutcome = null
        preferences.edit()
            .putString(KEY_ACTIVE_TASK_ID, taskId)
            .putInt(KEY_ACTIVE_TASK_SEQUENCE, 0)
            .apply()
        state.update {
            it.copy(
                activity = Activity.THINKING,
                lastRequest = request,
                reply = null,
                taskId = taskId,
                transcript = null,
                pendingAction = null,
                pendingApproval = null,
                approvalInFlight = false,
                connection = Connection.ONLINE,
                chatCenter = it.chatCenter.copy(sending = false),
            )
        }
        avatar.command(AvatarMode.THINKING, AvatarEmotion.CURIOUS, 0.6f)
        refreshActiveChat()
        followEvents(taskId)
    }

    private fun finishChatSendWithError(message: String) {
        state.update {
            it.copy(chatCenter = it.chatCenter.copy(sending = false, error = message))
        }
        failLocally(message)
    }

    /**
     * Re-reads the open transcript and its linked tasks so embedded task cards stay live.
     *
     * Deliberately quiet: this runs on a timer while a transcript is open, so it must not
     * raise the busy flag that would disable the composer under the user's fingers.
     */
    fun refreshActiveChat() {
        val chat = state.value.chatSession ?: return
        if (chatRefreshInFlight) return
        chatRefreshInFlight = true
        scope.launch {
            try {
                try {
                    val tasks = api.chatSessionTasks(chat.id)
                    val messages = api.chatMessages(chat.id)
                    val sessions = api.chatSessions()
                    val refreshedChat = sessions.firstOrNull { it.id == chat.id } ?: chat
                    state.update { current ->
                        if (current.chatSession?.id != chat.id) return@update current
                        current.copy(
                            chatSession = refreshedChat,
                            chatCenter = current.chatCenter.copy(
                                sessions = sessions,
                                messages = messages,
                                tasks = tasks,
                                error = null,
                            ),
                        )
                    }
                } catch (error: AssistantApiException) {
                    state.update {
                        it.copy(chatCenter = it.chatCenter.copy(error = error.message))
                    }
                }
            } finally {
                chatRefreshInFlight = false
            }
        }
    }

    private fun activateChat(chat: ChatSession) {
        preferences.edit().putString(KEY_CHAT_SESSION_ID, chat.id).apply()
        state.update { it.copy(chatSession = chat) }
    }

    private suspend fun ensureChatSession(): ChatSession {
        state.value.chatSession?.let { return it }
        val preferredId = preferences.getString(KEY_CHAT_SESSION_ID, null)
        val sessions = api.chatSessions()
        val chat = sessions.firstOrNull { it.id == preferredId } ?: api.createChatSession()
        activateChat(chat)
        state.update {
            it.copy(
                chatCenter = it.chatCenter.copy(
                    sessions = listOf(chat) + sessions.filterNot { item -> item.id == chat.id },
                    error = null,
                ),
            )
        }
        return chat
    }

    /** Refreshes the operational task list without disturbing the avatar conversation. */
    fun refreshTasks() {
        if (state.value.taskCenter.loading) return
        state.update {
            it.copy(taskCenter = it.taskCenter.copy(loading = true, error = null))
        }
        scope.launch {
            try {
                val tasks = api.tasks()
                state.update { current ->
                    val selectedId = current.taskCenter.selectedTask?.id
                    current.copy(
                        connection = Connection.ONLINE,
                        taskCenter = current.taskCenter.copy(
                            tasks = tasks,
                            loading = false,
                            selectedTask = tasks.firstOrNull { it.id == selectedId }
                                ?: current.taskCenter.selectedTask,
                        ),
                    )
                }
            } catch (error: AssistantApiException) {
                state.update {
                    it.copy(
                        connection = Connection.OFFLINE,
                        taskCenter = it.taskCenter.copy(
                            loading = false,
                            error = error.message ?: "Could not load tasks.",
                        ),
                    )
                }
            }
        }
    }

    fun openTaskDetails(taskId: String) {
        val task = state.value.taskCenter.tasks.firstOrNull { it.id == taskId }
            ?: state.value.chatCenter.tasks.firstOrNull { it.id == taskId }
            ?: return
        state.update {
            it.copy(
                taskCenter = it.taskCenter.copy(
                    selectedTask = task,
                    selectedEvents = emptyList(),
                    selectedApproval = null,
                    detailLoading = true,
                    error = null,
                ),
            )
        }
        reloadTaskDetails(taskId)
    }

    fun closeTaskDetails() {
        state.update {
            it.copy(
                taskCenter = it.taskCenter.copy(
                    selectedTask = null,
                    selectedEvents = emptyList(),
                    selectedApproval = null,
                    detailLoading = false,
                    actionInFlight = false,
                ),
            )
        }
    }

    fun refreshTaskDetails() {
        val taskId = state.value.taskCenter.selectedTask?.id ?: return
        reloadTaskDetails(taskId)
    }

    fun cancelTaskFromCenter() {
        val task = state.value.taskCenter.selectedTask?.takeIf { it.canCancel } ?: return
        if (state.value.taskCenter.actionInFlight) return
        state.update {
            it.copy(taskCenter = it.taskCenter.copy(actionInFlight = true, error = null))
        }
        scope.launch {
            try {
                api.cancel(task.id)
                refreshTaskCenterAfterAction(task.id)
            } catch (error: AssistantApiException) {
                finishTaskCenterActionWithError(error.message ?: "Could not cancel the task.")
            }
        }
    }

    fun approveTaskCenterPullRequest() = decideTaskCenterPullRequest(approve = true)

    fun rejectTaskCenterPullRequest() = decideTaskCenterPullRequest(approve = false)

    private fun decideTaskCenterPullRequest(approve: Boolean) {
        val taskCenter = state.value.taskCenter
        val task = taskCenter.selectedTask ?: return
        val approval = taskCenter.selectedApproval ?: return
        val token = approvalTokens.load()
        if (token.isNullOrBlank()) {
            state.update {
                it.copy(
                    hasApprovalToken = false,
                    taskCenter = it.taskCenter.copy(error = "Add the approval token in Settings first."),
                )
            }
            return
        }
        if (taskCenter.actionInFlight) return
        state.update {
            it.copy(taskCenter = it.taskCenter.copy(actionInFlight = true, error = null))
        }
        scope.launch {
            try {
                api.decidePullRequestApproval(
                    taskId = task.id,
                    approvalToken = token,
                    approve = approve,
                    expectedHeadSha = approval.expectedHeadSha,
                )
                refreshTaskCenterAfterAction(task.id)
            } catch (error: AssistantApiException) {
                finishTaskCenterActionWithError(error.message ?: "Could not update the approval.")
            }
        }
    }

    private fun reloadTaskDetails(taskId: String) {
        scope.launch {
            try {
                val task = api.task(taskId)
                val events = api.events(taskId)
                val approval = runCatching { api.pendingApproval(taskId) }.getOrNull()
                state.update { current ->
                    if (current.taskCenter.selectedTask?.id != taskId) return@update current
                    current.copy(
                        taskCenter = current.taskCenter.copy(
                            selectedTask = task,
                            selectedEvents = events,
                            selectedApproval = approval,
                            detailLoading = false,
                            actionInFlight = false,
                        ),
                    )
                }
            } catch (error: AssistantApiException) {
                state.update { current ->
                    if (current.taskCenter.selectedTask?.id != taskId) return@update current
                    current.copy(
                        taskCenter = current.taskCenter.copy(
                            detailLoading = false,
                            actionInFlight = false,
                            error = error.message ?: "Could not load task details.",
                        ),
                    )
                }
            }
        }
    }

    private suspend fun refreshTaskCenterAfterAction(taskId: String) {
        val tasks = api.tasks()
        val refreshedTask = tasks.firstOrNull { it.id == taskId }
        val events = api.events(taskId)
        val approval = runCatching { api.pendingApproval(taskId) }.getOrNull()
        state.update {
            it.copy(
                taskCenter = it.taskCenter.copy(
                    tasks = tasks,
                    selectedTask = refreshedTask,
                    selectedEvents = events,
                    selectedApproval = approval,
                    actionInFlight = false,
                    detailLoading = false,
                    error = null,
                ),
            )
        }
    }

    private fun finishTaskCenterActionWithError(message: String) {
        state.update {
            it.copy(
                taskCenter = it.taskCenter.copy(actionInFlight = false, error = message),
            )
        }
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
            val chat = try {
                ensureChatSession()
            } catch (error: AssistantApiException) {
                Log.w(TAG, "NATIVE_CHAT_CREATE_FAILED: ${error.message}")
                failLocally("I can't start a conversation with the assistant server.")
                return@launch
            }
            val taskId = try {
                api.createTask(request, UUID.randomUUID().toString(), chat.id)
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
            refreshActiveChat()
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
                refreshActiveChat()
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
        private const val KEY_CHAT_SESSION_ID = "chat_session_id"
        private const val KEY_ACTIVE_TASK_ID = "active_task_id"
        private const val KEY_ACTIVE_TASK_SEQUENCE = "active_task_sequence"
        private const val POLL_INTERVAL_MS = 500L
        private const val TASK_TIMEOUT_MS = 90_000L
        private const val MAX_REQUEST_CHARACTERS = 2_000
    }
}
