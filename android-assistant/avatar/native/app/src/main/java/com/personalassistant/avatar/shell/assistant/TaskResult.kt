package com.personalassistant.avatar.shell.assistant

import org.json.JSONArray
import org.json.JSONObject

data class TaskArtifact(
    val type: String,
    val label: String,
    val url: String?,
    val detail: String?,
)

data class TaskResult(
    val reply: String?,
    val summary: String?,
    val tests: List<String>,
    val notes: List<String>,
    val artifacts: List<TaskArtifact>,
    val provider: String?,
    val model: String?,
    val latencyMs: Long?,
    val inputTokens: Int?,
    val outputTokens: Int?,
    val validation: String,
    val failure: String?,
    val rawOutput: String?,
) {
    val hasContent: Boolean
        get() = reply != null || summary != null || artifacts.isNotEmpty() || failure != null
}

/** Builds a stable, user-facing result from the immutable task event stream. */
fun projectTaskResult(events: List<TaskEvent>): TaskResult {
    val executionOutput = events.lastOrNull { it.type == "EXECUTION_OUTPUT_RECEIVED" }
        ?.payload
        ?.optJSONObject("output")
    val assistantReply = events.lastOrNull { it.type == "ASSISTANT_REPLY" }
        ?.payload
        ?.optStringOrNull("text")
    val reply = assistantReply ?: executionOutput?.optStringOrNull("reply")
    val summary = executionOutput?.optStringOrNull("summary")?.takeUnless { it == reply }
    val usage = executionOutput?.optJSONObject("usage")

    val artifactPayloads = buildList {
        executionOutput?.optJSONArray("artifacts")?.let { artifacts ->
            repeat(artifacts.length()) { index ->
                artifacts.optJSONObject(index)?.let(::add)
            }
        }
        events.filter { it.type == "ARTIFACT_CREATED" }.forEach { add(it.payload) }
    }
    val artifacts = artifactPayloads
        .map(::projectArtifact)
        .distinctBy { listOf(it.type, it.url, it.label) }

    val failure = events.lastOrNull { it.type == "TASK_FAILED" }
        ?.payload
        ?.optStringOrNull("error")
    val validation = when {
        events.any { it.type == "VALIDATION_FAILED" } -> "Validation failed"
        events.any { it.type == "VALIDATION_SUCCEEDED" } -> "Validation passed"
        events.any { it.type == "VALIDATION_STARTED" } -> "Validation in progress"
        executionOutput != null -> "Result received"
        else -> "Waiting for a result"
    }

    return TaskResult(
        reply = reply,
        summary = summary,
        tests = executionOutput?.optStringList("tests").orEmpty(),
        notes = executionOutput?.optStringList("notes").orEmpty(),
        artifacts = artifacts,
        provider = executionOutput?.optStringOrNull("agent_provider")
            ?: executionOutput?.optStringOrNull("provider"),
        model = executionOutput?.optStringOrNull("model"),
        latencyMs = executionOutput?.optLong("latency_ms")?.takeIf { it > 0 },
        inputTokens = usage?.optInt("input_tokens")?.takeIf { it >= 0 },
        outputTokens = usage?.optInt("output_tokens")?.takeIf { it >= 0 },
        validation = validation,
        failure = failure,
        rawOutput = executionOutput?.let { output ->
            runCatching { output.toString(2) }.getOrElse { output.toString() }.take(MAX_RAW_OUTPUT)
        },
    )
}

private fun projectArtifact(payload: JSONObject): TaskArtifact {
    val type = payload.optString("type", "artifact")
    val number = payload.optInt("number").takeIf { it > 0 }
    val repository = payload.optStringOrNull("repository")
    val label = when (type) {
        "github_pull_request" -> "Pull request${number?.let { " #$it" }.orEmpty()}"
        "github_pull_request_merge" -> "Merged pull request${number?.let { " #$it" }.orEmpty()}"
        else -> type.replace('_', ' ').replaceFirstChar { it.uppercase() }
    }
    val detail = listOfNotNull(
        repository,
        payload.optStringOrNull("head_branch"),
        payload.optStringOrNull("head_sha")?.take(10),
        payload.optStringOrNull("merge_sha")?.take(10),
    ).joinToString(" · ").takeIf { it.isNotBlank() }
    return TaskArtifact(
        type = type,
        label = label,
        url = payload.optStringOrNull("url"),
        detail = detail,
    )
}

private fun JSONObject.optStringOrNull(key: String): String? =
    opt(key)
        ?.takeUnless { it == JSONObject.NULL }
        ?.toString()
        ?.trim()
        ?.takeIf { it.isNotEmpty() }

private fun JSONObject.optStringList(key: String): List<String> =
    optJSONArray(key)?.strings().orEmpty()

private fun JSONArray.strings(): List<String> = buildList {
    repeat(length()) { index ->
        opt(index)
            ?.takeUnless { it == JSONObject.NULL }
            ?.toString()
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?.let(::add)
    }
}

private const val MAX_RAW_OUTPUT = 20_000
