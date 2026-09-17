package com.personalassistant.avatar.shell.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.personalassistant.avatar.shell.assistant.AssistantTask
import com.personalassistant.avatar.shell.assistant.PendingPullRequestApproval
import com.personalassistant.avatar.shell.assistant.TaskCenterState
import com.personalassistant.avatar.shell.assistant.TaskEvent
import com.personalassistant.avatar.shell.assistant.TaskResult
import com.personalassistant.avatar.shell.assistant.projectTaskResult
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private val TaskBackground = Color(0xFF071116)
private val TaskSurface = Color(0xFF102127)
private val TaskSurfaceRaised = Color(0xFF172B33)
private val TaskAccent = Color(0xFF49D9C6)
private val TaskText = Color(0xFFE6F1F2)
private val TaskMuted = Color(0xFF9FB4B8)
private val TaskWarning = Color(0xFFFFC857)
private val TaskDanger = Color(0xFFFF8E86)

private enum class TaskFilter(val label: String) {
    ALL("All"),
    ACTIVE("Active"),
    REVIEW("Review"),
    FINISHED("Finished"),
}

@Composable
fun TaskCenterScreen(
    state: TaskCenterState,
    hasApprovalToken: Boolean,
    onRefresh: () -> Unit,
    onRefreshDetails: () -> Unit,
    onOpenTask: (String) -> Unit,
    onCloseTask: () -> Unit,
    onCancelTask: () -> Unit,
    onDiscussTask: (String) -> Unit,
    onOpenPullRequest: (String) -> Unit,
    onApprovePullRequest: () -> Unit,
    onRejectPullRequest: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Box(
        modifier = modifier
            .fillMaxSize()
            .background(TaskBackground)
            .statusBarsPadding()
            .padding(bottom = 72.dp),
    ) {
        if (state.selectedTask == null) {
            TaskList(
                state = state,
                onRefresh = onRefresh,
                onOpenTask = onOpenTask,
            )
        } else {
            TaskDetails(
                state = state,
                hasApprovalToken = hasApprovalToken,
                onBack = onCloseTask,
                onRefresh = onRefreshDetails,
                onCancelTask = onCancelTask,
                onDiscussTask = onDiscussTask,
                onOpenPullRequest = onOpenPullRequest,
                onApprovePullRequest = onApprovePullRequest,
                onRejectPullRequest = onRejectPullRequest,
            )
        }
    }
}

@Composable
private fun TaskList(
    state: TaskCenterState,
    onRefresh: () -> Unit,
    onOpenTask: (String) -> Unit,
) {
    var filter by remember { mutableStateOf(TaskFilter.ALL) }
    val visible = state.tasks.filter { task ->
        when (filter) {
            TaskFilter.ALL -> true
            TaskFilter.ACTIVE -> !task.isTerminal && !task.needsAttention
            TaskFilter.REVIEW -> task.needsAttention
            TaskFilter.FINISHED -> task.isTerminal
        }
    }
    val attentionCount = state.tasks.count { it.needsAttention }
    val activeCount = state.tasks.count { !it.isTerminal && !it.needsAttention }

    Column(modifier = Modifier.fillMaxSize()) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text("Task Center", color = TaskText, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                Text(
                    "$activeCount active  ·  $attentionCount need review",
                    color = if (attentionCount > 0) TaskWarning else TaskMuted,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            TextButton(onClick = onRefresh, enabled = !state.loading) {
                Text(if (state.loading) "Loading…" else "Refresh", color = TaskAccent)
            }
        }

        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            TaskFilter.entries.forEach { option ->
                FilterChip(
                    selected = filter == option,
                    onClick = { filter = option },
                    label = { Text(option.label) },
                )
            }
        }

        state.error?.let { InlineError(it) }

        when {
            state.loading && state.tasks.isEmpty() -> LoadingPane("Loading tasks…")
            visible.isEmpty() -> EmptyPane(
                if (state.tasks.isEmpty()) "No tasks yet" else "No tasks in this view",
                if (state.tasks.isEmpty()) "Ask the assistant to start one." else "Choose another filter.",
            )
            else -> LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                items(visible, key = { it.id }) { task ->
                    TaskCard(task = task, onClick = { onOpenTask(task.id) })
                }
            }
        }
    }
}

@Composable
private fun TaskCard(task: AssistantTask, onClick: () -> Unit) {
    val color = taskStatusColor(task.status)
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(TaskSurface, RoundedCornerShape(18.dp))
            .border(
                width = if (task.needsAttention) 1.dp else 0.dp,
                color = if (task.needsAttention) TaskWarning else Color.Transparent,
                shape = RoundedCornerShape(18.dp),
            )
            .clickable(onClick = onClick)
            .padding(16.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Box(modifier = Modifier.padding(top = 5.dp).size(10.dp).background(color, CircleShape))
        Spacer(modifier = Modifier.size(12.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = task.request.ifBlank { task.goal },
                color = TaskText,
                fontWeight = FontWeight.SemiBold,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(modifier = Modifier.height(5.dp))
            Text(
                "${taskStatusLabel(task.status)}  ·  ${formatTime(task.updatedAt)}",
                color = color,
                style = MaterialTheme.typography.bodySmall,
            )
            if (task.requiredCapabilities.isNotEmpty()) {
                Text(
                    task.requiredCapabilities.joinToString("  ·  "),
                    color = TaskMuted,
                    style = MaterialTheme.typography.labelSmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
        Text("›", color = TaskMuted, style = MaterialTheme.typography.headlineSmall)
    }
}

@Composable
private fun TaskDetails(
    state: TaskCenterState,
    hasApprovalToken: Boolean,
    onBack: () -> Unit,
    onRefresh: () -> Unit,
    onCancelTask: () -> Unit,
    onDiscussTask: (String) -> Unit,
    onOpenPullRequest: (String) -> Unit,
    onApprovePullRequest: () -> Unit,
    onRejectPullRequest: () -> Unit,
) {
    val task = state.selectedTask ?: return
    val result = remember(state.selectedEvents) { projectTaskResult(state.selectedEvents) }
    var confirmCancel by remember(task.id) { mutableStateOf(false) }

    Column(modifier = Modifier.fillMaxSize()) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            TextButton(onClick = onBack) { Text("‹ Tasks", color = TaskAccent) }
            Spacer(modifier = Modifier.weight(1f))
            TextButton(onClick = onRefresh, enabled = !state.detailLoading) {
                Text("Refresh", color = TaskAccent)
            }
        }

        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(start = 18.dp, end = 18.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                TaskHeader(task)
            }
            state.error?.let { message -> item { InlineError(message, padded = false) } }
            item {
                ResultPanel(
                    result = result,
                    loading = state.detailLoading,
                    onOpenArtifact = onOpenPullRequest,
                )
            }
            item {
                OutlinedButton(
                    onClick = { onDiscussTask(task.id) },
                    enabled = !state.actionInFlight,
                ) {
                    Text("Discuss in chat", color = TaskAccent)
                }
            }
            state.selectedApproval?.let { approval ->
                item {
                    ApprovalPanel(
                        approval = approval,
                        tokenConfigured = hasApprovalToken,
                        inFlight = state.actionInFlight,
                        onOpen = { onOpenPullRequest(approval.url) },
                        onApprove = onApprovePullRequest,
                        onReject = onRejectPullRequest,
                    )
                }
            }
            if (task.canCancel) {
                item {
                    OutlinedButton(onClick = { confirmCancel = true }, enabled = !state.actionInFlight) {
                        Text("Cancel task", color = TaskDanger)
                    }
                }
            }
            item {
                Text("Activity", color = TaskText, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            }
            when {
                state.detailLoading -> item { LoadingPane("Loading timeline…") }
                state.selectedEvents.isEmpty() -> item { EmptyPane("No events", "The task has not reported activity yet.") }
                else -> items(state.selectedEvents, key = { it.sequence }) { event -> EventRow(event) }
            }
        }
    }

    if (confirmCancel) {
        AlertDialog(
            onDismissRequest = { confirmCancel = false },
            title = { Text("Cancel this task?") },
            text = { Text("The worker will stop at its next cancellation checkpoint.") },
            confirmButton = {
                TextButton(onClick = {
                    confirmCancel = false
                    onCancelTask()
                }) { Text("Cancel task", color = TaskDanger) }
            },
            dismissButton = { TextButton(onClick = { confirmCancel = false }) { Text("Keep running") } },
        )
    }
}

@Composable
private fun ResultPanel(
    result: TaskResult,
    loading: Boolean,
    onOpenArtifact: (String) -> Unit,
) {
    var diagnosticsExpanded by remember(result.rawOutput) { mutableStateOf(false) }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(TaskSurface, RoundedCornerShape(20.dp))
            .padding(18.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Result", color = TaskText, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            Spacer(modifier = Modifier.weight(1f))
            Text(
                result.validation,
                color = when (result.validation) {
                    "Validation passed" -> Color(0xFF6BE38A)
                    "Validation failed" -> TaskDanger
                    else -> TaskMuted
                },
                style = MaterialTheme.typography.labelMedium,
            )
        }

        if (loading && !result.hasContent) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(color = TaskAccent, modifier = Modifier.size(20.dp))
                Spacer(modifier = Modifier.size(10.dp))
                Text("Loading the agent result…", color = TaskMuted)
            }
        } else if (!result.hasContent) {
            Text("The agent has not returned a result yet.", color = TaskMuted)
        }

        result.reply?.let {
            Text("FINAL ANSWER", color = TaskAccent, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold)
            SelectionContainer {
                Text(it, color = TaskText, style = MaterialTheme.typography.bodyLarge)
            }
        }

        result.summary?.let {
            Text("AGENT SUMMARY", color = TaskMuted, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold)
            SelectionContainer {
                Text(it, color = TaskText, style = MaterialTheme.typography.bodyMedium)
            }
        }

        val runtime = buildList {
            result.provider?.let { add(it) }
            result.model?.let { add(it) }
            result.latencyMs?.let { add("${it} ms") }
            if (result.inputTokens != null || result.outputTokens != null) {
                add("${result.inputTokens ?: 0} in / ${result.outputTokens ?: 0} out")
            }
        }
        if (runtime.isNotEmpty()) {
            Text(runtime.joinToString("  ·  "), color = TaskMuted, style = MaterialTheme.typography.bodySmall)
        }

        if (result.tests.isNotEmpty()) {
            ResultList(title = "TESTS", items = result.tests, positive = true)
        }
        if (result.notes.isNotEmpty()) {
            ResultList(title = "NOTES", items = result.notes, positive = false)
        }

        result.failure?.let {
            Text("ERROR", color = TaskDanger, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold)
            SelectionContainer {
                Text(
                    it,
                    color = TaskDanger,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.fillMaxWidth().background(Color(0xFF3A2022), RoundedCornerShape(12.dp)).padding(12.dp),
                )
            }
        }

        result.artifacts.forEach { artifact ->
            Row(
                modifier = Modifier.fillMaxWidth().background(TaskSurfaceRaised, RoundedCornerShape(14.dp)).padding(12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(artifact.label, color = TaskText, fontWeight = FontWeight.SemiBold)
                    artifact.detail?.let { Text(it, color = TaskMuted, style = MaterialTheme.typography.bodySmall) }
                }
                artifact.url?.let { url ->
                    TextButton(onClick = { onOpenArtifact(url) }) { Text("Open", color = TaskAccent) }
                }
            }
        }

        result.rawOutput?.let { raw ->
            TextButton(onClick = { diagnosticsExpanded = !diagnosticsExpanded }) {
                Text(if (diagnosticsExpanded) "Hide agent output" else "Show agent output", color = TaskAccent)
            }
            if (diagnosticsExpanded) {
                SelectionContainer {
                    Text(
                        raw,
                        color = Color(0xFFC4D6D9),
                        fontFamily = FontFamily.Monospace,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(Color(0xFF071116), RoundedCornerShape(12.dp))
                            .padding(12.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun ResultList(title: String, items: List<String>, positive: Boolean) {
    Text(title, color = TaskMuted, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold)
    items.forEach { item ->
        Text(
            "${if (positive) "✓" else "•"}  $item",
            color = if (positive) Color(0xFFB7E9C3) else TaskText,
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

@Composable
private fun TaskHeader(task: AssistantTask) {
    val color = taskStatusColor(task.status)
    Column(
        modifier = Modifier.fillMaxWidth().background(TaskSurface, RoundedCornerShape(20.dp)).padding(18.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(modifier = Modifier.size(10.dp).background(color, CircleShape))
            Spacer(modifier = Modifier.size(8.dp))
            Text(taskStatusLabel(task.status), color = color, fontWeight = FontWeight.Bold)
        }
        Spacer(modifier = Modifier.height(10.dp))
        Text(task.request, color = TaskText, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
        if (task.goal.isNotBlank() && task.goal != task.request) {
            Spacer(modifier = Modifier.height(6.dp))
            Text(task.goal, color = TaskMuted, style = MaterialTheme.typography.bodyMedium)
        }
        Spacer(modifier = Modifier.height(10.dp))
        Text("Updated ${formatTime(task.updatedAt)}", color = TaskMuted, style = MaterialTheme.typography.bodySmall)
        task.claimedBy?.let { Text("Worker $it", color = TaskMuted, style = MaterialTheme.typography.bodySmall) }
        Text("ID ${task.id.take(12)}", color = TaskMuted, style = MaterialTheme.typography.labelSmall)
    }
}

@Composable
private fun ApprovalPanel(
    approval: PendingPullRequestApproval,
    tokenConfigured: Boolean,
    inFlight: Boolean,
    onOpen: () -> Unit,
    onApprove: () -> Unit,
    onReject: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(TaskSurfaceRaised, RoundedCornerShape(18.dp))
            .border(1.dp, TaskWarning, RoundedCornerShape(18.dp))
            .padding(16.dp),
    ) {
        Text("REVIEW NEEDED", color = TaskWarning, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold)
        Text("${approval.repository} · PR #${approval.number}", color = TaskText, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        Text("Commit ${approval.shortSha} · ${if (approval.draft) "Draft" else "Ready"}", color = TaskMuted, style = MaterialTheme.typography.bodySmall)
        Spacer(modifier = Modifier.height(12.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = onOpen, enabled = !inFlight) { Text("GitHub", color = TaskAccent) }
            Button(
                onClick = onApprove,
                enabled = tokenConfigured && !inFlight,
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF168C87)),
            ) { Text(if (inFlight) "Checking…" else "Approve") }
            OutlinedButton(onClick = onReject, enabled = tokenConfigured && !inFlight) { Text("Reject", color = TaskDanger) }
        }
        if (!tokenConfigured) {
            Text("Add the approval token in Assistant settings.", color = TaskWarning, style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun EventRow(event: TaskEvent) {
    val (title, description) = eventPresentation(event)
    Row(modifier = Modifier.fillMaxWidth()) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Box(modifier = Modifier.size(10.dp).background(eventColor(event.type), CircleShape))
            Box(modifier = Modifier.padding(top = 4.dp).size(width = 2.dp, height = 42.dp).background(Color(0xFF294047)))
        }
        Spacer(modifier = Modifier.size(12.dp))
        Column(modifier = Modifier.weight(1f).padding(bottom = 8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(title, color = TaskText, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                Text(formatTime(event.createdAt), color = TaskMuted, style = MaterialTheme.typography.labelSmall)
            }
            description?.takeIf { it.isNotBlank() }?.let {
                Text(it, color = TaskMuted, style = MaterialTheme.typography.bodySmall, maxLines = 4, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

@Composable
private fun InlineError(message: String, padded: Boolean = true) {
    Text(
        message,
        color = TaskDanger,
        style = MaterialTheme.typography.bodySmall,
        modifier = Modifier
            .fillMaxWidth()
            .then(if (padded) Modifier.padding(horizontal = 18.dp, vertical = 8.dp) else Modifier)
            .background(Color(0xFF3A2022), RoundedCornerShape(12.dp))
            .padding(12.dp),
    )
}

@Composable
private fun LoadingPane(label: String) {
    Column(
        modifier = Modifier.fillMaxWidth().padding(28.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        CircularProgressIndicator(color = TaskAccent, modifier = Modifier.size(28.dp))
        Spacer(modifier = Modifier.height(12.dp))
        Text(label, color = TaskMuted)
    }
}

@Composable
private fun EmptyPane(title: String, subtitle: String) {
    Column(
        modifier = Modifier.fillMaxWidth().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(title, color = TaskText, fontWeight = FontWeight.Bold)
        Text(subtitle, color = TaskMuted, style = MaterialTheme.typography.bodySmall)
    }
}

private fun taskStatusLabel(status: String): String = when (status) {
    "created" -> "Queued"
    "planning" -> "Planning"
    "waiting" -> "Waiting"
    "executing" -> "Running"
    "validating" -> "Validating"
    "waiting_for_approval" -> "Review needed"
    "completed" -> "Completed"
    "failed" -> "Failed"
    "cancelled" -> "Cancelled"
    else -> status.replace('_', ' ').replaceFirstChar { it.uppercase() }
}

private fun taskStatusColor(status: String): Color = when (status) {
    "waiting_for_approval" -> TaskWarning
    "completed" -> Color(0xFF6BE38A)
    "failed" -> TaskDanger
    "cancelled" -> TaskMuted
    "created", "planning", "waiting", "executing", "validating" -> Color(0xFF8CB8FF)
    else -> TaskMuted
}

private fun eventColor(type: String): Color = when (type) {
    "TASK_COMPLETED", "VALIDATION_SUCCEEDED" -> Color(0xFF6BE38A)
    "TASK_FAILED", "VALIDATION_FAILED" -> TaskDanger
    "TASK_CANCELLED", "APPROVAL_REJECTED" -> TaskMuted
    "APPROVAL_REQUESTED" -> TaskWarning
    else -> TaskAccent
}

private fun eventPresentation(event: TaskEvent): Pair<String, String?> = when (event.type) {
    "TASK_CREATED" -> "Task created" to event.payload.optString("goal")
    "TASK_PLANNING_STARTED" -> "Planning started" to null
    "PLAN_CREATED" -> "Plan prepared" to event.payload.optJSONArray("steps")?.let { steps ->
        List(steps.length()) { steps.optString(it) }.joinToString(" · ")
    }
    "TASK_ANALYZED" -> "Request analyzed" to null
    "MANAGER_DECISION_CREATED" -> "Manager selected a route" to event.payload.optString("reason")
    "CAPABILITY_SELECTED" -> "Agent selected" to listOf(
        event.payload.optString("capability_id"),
        event.payload.optString("executor_id"),
    ).filter { it.isNotBlank() }.joinToString(" · ")
    "EXECUTION_STARTED" -> "Work started" to event.payload.optString("executor_id")
    "EXECUTION_OUTPUT_RECEIVED" -> "Agent returned a result" to null
    "VALIDATION_STARTED" -> "Validating result" to null
    "VALIDATION_SUCCEEDED" -> "Validation passed" to null
    "VALIDATION_FAILED" -> "Validation failed" to null
    "ARTIFACT_CREATED" -> "Artifact created" to when (event.payload.optString("type")) {
        "github_pull_request" -> "GitHub PR #${event.payload.optInt("number")}"
        else -> event.payload.optString("type")
    }
    "APPROVAL_REQUESTED" -> "Review requested" to "PR #${event.payload.optInt("number")} · commit ${event.payload.optString("expected_head_sha").take(10)}"
    "APPROVAL_GRANTED" -> "Approval granted" to null
    "APPROVAL_REJECTED" -> "Approval rejected" to null
    "TOOL_CALLED" -> "External action started" to event.payload.optString("operation")
    "TOOL_RESULT_RECEIVED" -> "External action finished" to event.payload.optString("operation")
    "USER_MESSAGE_RECEIVED" -> "You added a note" to event.payload.optString("message")
    "ASSISTANT_REPLY" -> "Assistant replied" to event.payload.optString("text")
    "TASK_COMPLETED" -> "Task completed" to null
    "TASK_FAILED" -> "Task failed" to null
    "TASK_CANCELLED" -> "Task cancelled" to null
    else -> event.type.replace('_', ' ').lowercase().replaceFirstChar { it.uppercase() } to null
}

private fun formatTime(raw: String): String {
    if (raw.isBlank()) return ""
    return runCatching {
        OffsetDateTime.parse(raw)
            .atZoneSameInstant(ZoneId.systemDefault())
            .format(DateTimeFormatter.ofPattern("MMM d, HH:mm"))
    }.getOrDefault(raw.take(16).replace('T', ' '))
}
