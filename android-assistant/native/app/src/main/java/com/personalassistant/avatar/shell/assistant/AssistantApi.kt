package com.personalassistant.avatar.shell.assistant

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/** One entry of a task's ordered event stream (`GET /tasks/{id}/events`). */
data class TaskEvent(val sequence: Int, val type: String, val payload: JSONObject)

class AssistantApiException(message: String, cause: Throwable? = null) : IOException(message, cause)

/**
 * Minimal client for the Personal Assistant HTTP API. Blocking I/O runs on [Dispatchers.IO].
 */
class AssistantApi(baseUrl: String) {
    private val base = baseUrl.trim().trimEnd('/')

    suspend fun health(): Boolean = try {
        request("GET", "/health").optString("status") == "ok"
    } catch (_: IOException) {
        false
    }

    /**
     * Creates a task. [clientKey] makes retries idempotent; [conversationId] lets the backend
     * include earlier turns of the same conversation as context.
     */
    suspend fun createTask(request: String, clientKey: String, conversationId: String): String {
        val now = java.time.ZonedDateTime.now()
        val body = JSONObject()
            .put("request", request)
            .put("external_source", "android")
            .put("external_key", clientKey)
            .put(
                "source_context",
                JSONObject()
                    .put("client", "android-avatar")
                    .put("conversation_id", conversationId)
                    .put("local_time", now.withNano(0).toOffsetDateTime().toString())
                    .put("timezone", now.zone.id),
            )
        return request("POST", "/tasks", body).getString("id")
    }

    suspend fun events(taskId: String): List<TaskEvent> {
        val events: JSONArray = request("GET", "/tasks/$taskId/events").getJSONArray("events")
        return List(events.length()) { index ->
            val event = events.getJSONObject(index)
            TaskEvent(event.getInt("sequence"), event.getString("event_type"), event.optJSONObject("payload") ?: JSONObject())
        }
    }

    /** Adds a note to the task's event stream, e.g. the outcome of a confirmed phone action. */
    suspend fun addMessage(taskId: String, message: String) {
        request("POST", "/tasks/$taskId/messages", JSONObject().put("message", message))
    }

    suspend fun cancel(taskId: String) {
        request("POST", "/tasks/$taskId/cancel")
    }

    private suspend fun request(method: String, path: String, body: JSONObject? = null): JSONObject =
        withContext(Dispatchers.IO) {
            val connection = try {
                URL(base + path).openConnection() as HttpURLConnection
            } catch (error: Exception) {
                throw AssistantApiException("Invalid backend URL: $base", error)
            }
            try {
                connection.requestMethod = method
                connection.connectTimeout = 4_000
                connection.readTimeout = 8_000
                connection.setRequestProperty("Accept", "application/json")
                if (body != null) {
                    connection.doOutput = true
                    connection.setRequestProperty("Content-Type", "application/json")
                    connection.outputStream.use { it.write(body.toString().toByteArray()) }
                }
                val code = connection.responseCode
                val stream = if (code in 200..299) connection.inputStream else connection.errorStream
                val text = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
                if (code !in 200..299) throw AssistantApiException("HTTP $code for $method $path")
                if (text.isBlank()) JSONObject() else JSONObject(text)
            } catch (error: AssistantApiException) {
                throw error
            } catch (error: Exception) {
                throw AssistantApiException("Cannot reach $base", error)
            } finally {
                connection.disconnect()
            }
        }
}
