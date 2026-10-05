package com.personalassistant.avatar.shell.assistant

import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/** Compact task representation used by the native task center. */
data class AssistantTask(
    val id: String,
    val request: String,
    val goal: String,
    val status: String,
    val requiredCapabilities: List<String>,
    val chatSessionId: String?,
    val claimedBy: String?,
    val createdAt: String,
    val updatedAt: String,
) {
    val needsAttention: Boolean get() = status == "waiting_for_approval"
    val isTerminal: Boolean get() = status in setOf("completed", "failed", "cancelled")
    val canCancel: Boolean get() = !isTerminal && !needsAttention

    companion object {
        fun fromJson(payload: JSONObject): AssistantTask {
            val capabilities = payload.optJSONArray("required_capabilities") ?: JSONArray()
            return AssistantTask(
                id = payload.getString("id"),
                request = payload.optString("original_request"),
                goal = payload.optString("current_goal"),
                status = payload.optString("status"),
                requiredCapabilities = List(capabilities.length()) { capabilities.getString(it) },
                chatSessionId = payload.opt("chat_session_id")
                    ?.takeUnless { it == JSONObject.NULL }
                    ?.toString(),
                claimedBy = payload.opt("claimed_by")
                    ?.takeUnless { it == JSONObject.NULL }
                    ?.toString()
                    ?.takeIf { it.isNotBlank() },
                createdAt = payload.optString("created_at"),
                updatedAt = payload.optString("updated_at"),
            )
        }
    }
}

data class ChatSession(
    val id: String,
    val title: String,
    val createdAt: String,
    val updatedAt: String,
) {
    companion object {
        fun fromJson(payload: JSONObject): ChatSession = ChatSession(
            id = payload.getString("id"),
            title = payload.optString("title", "New conversation"),
            createdAt = payload.optString("created_at"),
            updatedAt = payload.optString("updated_at"),
        )
    }
}

data class ChatMessage(
    val id: String,
    val role: String,
    val content: String,
    val linkedTaskId: String?,
    val createdAt: String,
) {
    companion object {
        fun fromJson(payload: JSONObject): ChatMessage = ChatMessage(
            id = payload.getString("id"),
            role = payload.optString("role"),
            content = payload.optString("content"),
            linkedTaskId = payload.opt("linked_task_id")
                ?.takeUnless { it == JSONObject.NULL }
                ?.toString(),
            createdAt = payload.optString("created_at"),
        )
    }
}

/**
 * A task the backend suggests for a message, which only runs once the user confirms it.
 * [consequential] marks work that changes code or infrastructure.
 */
data class TaskProposal(
    val reason: String,
    val suggestedGoal: String,
    val requiredCapabilities: List<String>,
    val consequential: Boolean,
) {
    companion object {
        fun fromJson(payload: JSONObject): TaskProposal {
            val capabilities = payload.optJSONArray("required_capabilities") ?: JSONArray()
            return TaskProposal(
                reason = payload.optString("reason"),
                suggestedGoal = payload.optString("suggested_goal"),
                requiredCapabilities = List(capabilities.length()) { capabilities.getString(it) },
                consequential = payload.optBoolean("consequential", true),
            )
        }
    }
}

/** The stored chat turn plus the task, if any, the backend would propose for it. */
data class PostedChatMessage(val message: ChatMessage, val proposal: TaskProposal?)

/** One entry of a task's ordered event stream (`GET /tasks/{id}/events`). */
data class TaskEvent(
    val sequence: Int,
    val type: String,
    val payload: JSONObject,
    val createdAt: String,
)

/** A merge action that is paused until the user reviews the exact pull-request commit. */
data class PendingPullRequestApproval(
    val repository: String,
    val number: Int,
    val url: String,
    val expectedHeadSha: String,
    val draft: Boolean,
) {
    val shortSha: String get() = expectedHeadSha.take(10)

    companion object {
        fun fromJson(payload: JSONObject): PendingPullRequestApproval? {
            if (payload.optString("type") != "github_pull_request_merge") return null
            val repository = payload.optString("repository").trim()
            val number = payload.optInt("number")
            val url = payload.optString("url").trim()
            val sha = payload.optString("expected_head_sha").trim()
            val validSha = sha.length in setOf(40, 64) && sha.all { it in '0'..'9' || it in 'a'..'f' }
            val validUrl = runCatching {
                val parsed = URL(url)
                parsed.protocol == "https" && parsed.host.equals("github.com", ignoreCase = true)
            }.getOrDefault(false)
            if (repository.isEmpty() || number <= 0 || !validUrl || !validSha) return null
            return PendingPullRequestApproval(
                repository = repository,
                number = number,
                url = url,
                expectedHeadSha = sha,
                draft = payload.optBoolean("draft", true),
            )
        }
    }
}

class AssistantApiException(
    message: String,
    cause: Throwable? = null,
    val statusCode: Int? = null,
) : IOException(message, cause)

/**
 * Minimal client for the Personal Assistant HTTP API. Blocking I/O runs on [Dispatchers.IO].
 */
class AssistantApi(baseUrl: String, private val speechCacheDirectory: File) {
    private val base = baseUrl.trim().trimEnd('/')

    suspend fun health(): Boolean = try {
        request("GET", "/health").optString("status") == "ok"
    } catch (_: IOException) {
        false
    }

    /**
     * Creates a task. [clientKey] makes retries idempotent; [chatSessionId] lets the backend
     * include earlier turns of the persistent conversation as context.
     */
    suspend fun createTask(request: String, clientKey: String, chatSessionId: String): String {
        val now = java.time.ZonedDateTime.now()
        val body = JSONObject()
            .put("request", request)
            .put("external_source", "android")
            .put("external_key", clientKey)
            .put("chat_session_id", chatSessionId)
            .put(
                "source_context",
                JSONObject()
                    .put("client", "android-avatar")
                    .put("local_time", now.withNano(0).toOffsetDateTime().toString())
                    .put("timezone", now.zone.id),
            )
        return request("POST", "/tasks", body).getString("id")
    }

    suspend fun createChatSession(): ChatSession =
        ChatSession.fromJson(request("POST", "/chat-sessions", JSONObject()))

    suspend fun chatSessions(limit: Int = 100): List<ChatSession> {
        val sessions = request(
            "GET",
            "/chat-sessions?limit=${limit.coerceIn(1, 500)}",
        ).getJSONArray("sessions")
        return List(sessions.length()) { index -> ChatSession.fromJson(sessions.getJSONObject(index)) }
    }

    suspend fun chatSessionTasks(chatSessionId: String, limit: Int = 100): List<AssistantTask> {
        val tasks = request(
            "GET",
            "/chat-sessions/$chatSessionId/tasks?limit=${limit.coerceIn(1, 500)}",
        ).getJSONArray("tasks")
        return List(tasks.length()) { index -> AssistantTask.fromJson(tasks.getJSONObject(index)) }
    }

    suspend fun chatMessages(chatSessionId: String, limit: Int = 500): List<ChatMessage> {
        val messages = request(
            "GET",
            "/chat-sessions/$chatSessionId/messages?limit=${limit.coerceIn(1, 500)}",
        ).getJSONArray("messages")
        return List(messages.length()) { index -> ChatMessage.fromJson(messages.getJSONObject(index)) }
    }

    /** Records a turn of conversation. This never launches work on its own. */
    suspend fun postChatMessage(chatSessionId: String, content: String): PostedChatMessage {
        val payload = request(
            "POST",
            "/chat-sessions/$chatSessionId/messages",
            JSONObject().put("content", content),
        )
        return PostedChatMessage(
            message = ChatMessage.fromJson(payload.getJSONObject("message")),
            proposal = payload.optJSONObject("proposal")?.let(TaskProposal::fromJson),
        )
    }

    /** Confirms a proposal: launches the work the message asked for. */
    suspend fun createTaskFromMessage(
        chatSessionId: String,
        messageId: String,
        goal: String? = null,
        requiredCapabilities: List<String> = emptyList(),
    ): AssistantTask {
        val now = java.time.ZonedDateTime.now()
        val body = JSONObject()
            .put("required_capabilities", JSONArray(requiredCapabilities))
            .put(
                "source_context",
                JSONObject()
                    .put("client", "android-avatar")
                    .put("local_time", now.withNano(0).toOffsetDateTime().toString())
                    .put("timezone", now.zone.id),
            )
        goal?.takeIf { it.isNotBlank() }?.let { body.put("goal", it) }
        return AssistantTask.fromJson(
            request("POST", "/chat-sessions/$chatSessionId/messages/$messageId/task", body),
        )
    }

    /** Starts a new task that continues an earlier one, carrying only its curated context. */
    suspend fun createFollowUpTask(taskId: String, request: String): AssistantTask {
        val now = java.time.ZonedDateTime.now()
        val body = JSONObject()
            .put("request", request)
            .put(
                "source_context",
                JSONObject()
                    .put("client", "android-avatar")
                    .put("local_time", now.withNano(0).toOffsetDateTime().toString())
                    .put("timezone", now.zone.id),
            )
        return AssistantTask.fromJson(request("POST", "/tasks/$taskId/follow-up", body))
    }

    suspend fun ensureTaskChatSession(taskId: String): ChatSession =
        ChatSession.fromJson(request("POST", "/tasks/$taskId/chat-session"))

    suspend fun tasks(limit: Int = 100): List<AssistantTask> {
        val tasks = request("GET", "/tasks?limit=${limit.coerceIn(1, 500)}").getJSONArray("tasks")
        return List(tasks.length()) { index -> AssistantTask.fromJson(tasks.getJSONObject(index)) }
    }

    suspend fun task(taskId: String): AssistantTask =
        AssistantTask.fromJson(request("GET", "/tasks/$taskId"))

    suspend fun events(taskId: String): List<TaskEvent> {
        val events: JSONArray = request("GET", "/tasks/$taskId/events").getJSONArray("events")
        return List(events.length()) { index ->
            val event = events.getJSONObject(index)
            TaskEvent(
                sequence = event.getInt("sequence"),
                type = event.getString("event_type"),
                payload = event.optJSONObject("payload") ?: JSONObject(),
                createdAt = event.optString("created_at"),
            )
        }
    }

    /** Returns the current typed approval, or null when this task is not at an approval gate. */
    suspend fun pendingApproval(taskId: String): PendingPullRequestApproval? = try {
        val payload = request("GET", "/tasks/$taskId/pending-approval")
        PendingPullRequestApproval.fromJson(payload)
            ?: throw AssistantApiException("Backend returned an invalid approval")
    } catch (error: AssistantApiException) {
        if (error.statusCode == 404) null else throw error
    }

    /** Adds a note to the task's event stream, e.g. the outcome of a confirmed phone action. */
    suspend fun addMessage(taskId: String, message: String) {
        request("POST", "/tasks/$taskId/messages", JSONObject().put("message", message))
    }

    suspend fun cancel(taskId: String) {
        request("POST", "/tasks/$taskId/cancel")
    }

    /** Approves or rejects the exact pending PR. The secret is supplied only at call time. */
    suspend fun decidePullRequestApproval(
        taskId: String,
        approvalToken: String,
        approve: Boolean,
        expectedHeadSha: String,
    ) {
        if (approvalToken.isBlank()) throw AssistantApiException("Approval token is not configured")
        val body = JSONObject().put("decision", if (approve) "approve" else "reject")
        if (approve) {
            body.put("expected_head_sha", expectedHeadSha)
            body.put("merge_method", "squash")
        }
        request(
            method = "POST",
            path = "/tasks/$taskId/pull-request-approval",
            body = body,
            headers = mapOf("X-Assistant-Approval-Token" to approvalToken),
        )
    }

    /** Downloads a cloud-generated WAV into app-private cache and returns its canonical path. */
    suspend fun speech(text: String, emotion: String): String = withContext(Dispatchers.IO) {
        if (text.length > MAX_SPEECH_CHARACTERS) {
            throw AssistantApiException("Reply is too long for cloud speech")
        }
        val body = JSONObject().put("text", text).put("emotion", emotion)
        val connection = open("/speech")
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 4_000
            connection.readTimeout = 35_000
            connection.doOutput = true
            connection.setRequestProperty("Accept", "audio/wav")
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(body.toString().toByteArray()) }
            val code = connection.responseCode
            if (code !in 200..299) throw AssistantApiException("HTTP $code for POST /speech")
            val declared = connection.contentLengthLong
            if (declared > MAX_AUDIO_BYTES) throw AssistantApiException("Cloud speech was too large")
            val audio = connection.inputStream.use { it.readBytes() }
            if (audio.size !in 44..MAX_AUDIO_BYTES || !audio.copyOfRange(0, 4).contentEquals("RIFF".toByteArray())) {
                throw AssistantApiException("Cloud speech was not a valid WAV")
            }
            val directory = File(speechCacheDirectory, "assistant-speech")
            if (!directory.isDirectory && !directory.mkdirs()) {
                throw AssistantApiException("Speech cache is unavailable")
            }
            directory.listFiles()?.forEach { stale ->
                if (!stale.delete()) stale.deleteOnExit()
            }
            File(directory, "${UUID.randomUUID()}.wav").apply { writeBytes(audio) }.canonicalPath
        } catch (error: AssistantApiException) {
            throw error
        } catch (error: Exception) {
            throw AssistantApiException("Cloud speech is unavailable", error)
        } finally {
            connection.disconnect()
        }
    }

    private suspend fun request(
        method: String,
        path: String,
        body: JSONObject? = null,
        headers: Map<String, String> = emptyMap(),
    ): JSONObject =
        withContext(Dispatchers.IO) {
            val connection = open(path)
            try {
                connection.requestMethod = method
                connection.connectTimeout = 4_000
                connection.readTimeout = 8_000
                connection.setRequestProperty("Accept", "application/json")
                headers.forEach(connection::setRequestProperty)
                if (body != null) {
                    connection.doOutput = true
                    connection.setRequestProperty("Content-Type", "application/json")
                    connection.outputStream.use { it.write(body.toString().toByteArray()) }
                }
                val code = connection.responseCode
                val stream = if (code in 200..299) connection.inputStream else connection.errorStream
                val text = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
                if (code !in 200..299) {
                    val detail = runCatching { JSONObject(text).optString("detail") }
                        .getOrNull()
                        ?.takeIf { it.isNotBlank() }
                        ?.take(240)
                    throw AssistantApiException(
                        detail ?: "HTTP $code for $method $path",
                        statusCode = code,
                    )
                }
                if (text.isBlank()) JSONObject() else JSONObject(text)
            } catch (error: AssistantApiException) {
                throw error
            } catch (error: Exception) {
                throw AssistantApiException("Cannot reach $base", error)
            } finally {
                connection.disconnect()
            }
        }

    private fun open(path: String): HttpURLConnection = try {
        URL(base + path).openConnection() as HttpURLConnection
    } catch (error: Exception) {
        throw AssistantApiException("Invalid backend URL: $base", error)
    }

    private companion object {
        const val MAX_SPEECH_CHARACTERS = 600
        const val MAX_AUDIO_BYTES = 12 * 1024 * 1024
    }
}
