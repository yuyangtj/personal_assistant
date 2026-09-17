package com.personalassistant.avatar.shell.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.personalassistant.avatar.shell.assistant.AssistantTask
import com.personalassistant.avatar.shell.assistant.ChatCenterState
import com.personalassistant.avatar.shell.assistant.ChatMessage
import com.personalassistant.avatar.shell.assistant.ChatSession
import com.personalassistant.avatar.shell.assistant.TaskProposal

private val ThreadBackground = Color(0xFF071116)
private val ThreadSurface = Color(0xFF102127)
private val ThreadUser = Color(0xFF145E5A)
private val ThreadAccent = Color(0xFF49D9C6)
private val ThreadText = Color(0xFFE6F1F2)
private val ThreadMuted = Color(0xFF9FB4B8)
private val ThreadReference = Color(0xFF16303A)
private val ThreadAttention = Color(0xFFFFC46B)

@Composable
fun ChatThreadScreen(
    chat: ChatSession,
    state: ChatCenterState,
    assistantName: String,
    isBusy: Boolean,
    onBack: () -> Unit,
    onRefresh: () -> Unit,
    onSend: (String) -> Unit,
    onOpenTask: (String) -> Unit,
    onCreateTask: (String) -> Unit,
    onConfirmProposal: () -> Unit,
    onDismissProposal: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var prompt by remember(chat.id) { mutableStateOf("") }
    val listState = rememberLazyListState()
    val tasksById = remember(state.tasks) { state.tasks.associateBy { it.id } }

    LaunchedEffect(state.messages.size) {
        if (state.messages.isNotEmpty()) listState.animateScrollToItem(state.messages.lastIndex)
    }

    val sending = state.sending

    fun submit() {
        val message = prompt.trim()
        if (message.isEmpty() || isBusy || sending) return
        onSend(message)
        prompt = ""
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(ThreadBackground)
            .statusBarsPadding()
            .imePadding()
            .padding(bottom = 72.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            TextButton(onClick = onBack) { Text("‹ Chats", color = ThreadAccent) }
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    chat.title,
                    color = ThreadText,
                    fontWeight = FontWeight.Bold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text("Persistent conversation", color = ThreadMuted, style = MaterialTheme.typography.labelSmall)
            }
            TextButton(onClick = onRefresh, enabled = !state.loading) {
                Text("Refresh", color = ThreadAccent)
            }
        }

        state.error?.let {
            Text(
                it,
                color = Color(0xFFFF8E86),
                modifier = Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 6.dp),
                style = MaterialTheme.typography.bodySmall,
            )
        }

        when {
            state.loading && state.messages.isEmpty() -> Column(
                modifier = Modifier.weight(1f).fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                CircularProgressIndicator(color = ThreadAccent)
                Text("Loading conversation…", color = ThreadMuted, modifier = Modifier.padding(top = 12.dp))
            }
            state.messages.isEmpty() -> Column(
                modifier = Modifier.weight(1f).fillMaxWidth().padding(32.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                Text("Start the conversation", color = ThreadText, fontWeight = FontWeight.Bold)
                Text("Messages and linked tasks will appear here.", color = ThreadMuted)
            }
            else -> LazyColumn(
                state = listState,
                modifier = Modifier.weight(1f).fillMaxWidth(),
                contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                items(state.messages, key = { it.id }) { message ->
                    MessageBubble(
                        message = message,
                        assistantName = assistantName,
                        task = message.linkedTaskId?.let(tasksById::get),
                        canCreateTask = message.role == "user" &&
                            message.linkedTaskId == null &&
                            !isBusy &&
                            !sending,
                        onOpenTask = onOpenTask,
                        onCreateTask = onCreateTask,
                    )
                }
            }
        }

        state.proposal?.let { proposal ->
            ProposalCard(
                proposal = proposal,
                enabled = !isBusy && !sending,
                onConfirm = onConfirmProposal,
                onDismiss = onDismissProposal,
            )
        }

        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalAlignment = Alignment.Bottom,
        ) {
            OutlinedTextField(
                value = prompt,
                onValueChange = { prompt = it.take(2_000) },
                modifier = Modifier.weight(1f),
                placeholder = { Text("Continue this conversation…") },
                maxLines = 4,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { submit() }),
            )
            Button(
                onClick = ::submit,
                enabled = prompt.isNotBlank() && !isBusy && !sending,
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF168C87)),
            ) { Text(if (isBusy || sending) "Working…" else "Send") }
        }
    }
}

@Composable
private fun MessageBubble(
    message: ChatMessage,
    assistantName: String,
    task: AssistantTask?,
    canCreateTask: Boolean,
    onOpenTask: (String) -> Unit,
    onCreateTask: (String) -> Unit,
) {
    val isUser = message.role == "user"
    val isReference = message.role == "system"
    Column(
        modifier = Modifier.fillMaxWidth(),
        horizontalAlignment = when {
            isUser -> Alignment.End
            isReference -> Alignment.CenterHorizontally
            else -> Alignment.Start
        },
    ) {
        Text(
            when {
                isUser -> "YOU"
                isReference -> "TASK REFERENCE"
                else -> assistantName.uppercase()
            },
            color = ThreadMuted,
            style = MaterialTheme.typography.labelSmall,
        )
        Column(
            modifier = Modifier
                .fillMaxWidth(if (isReference) 1f else 0.88f)
                .background(
                    when {
                        isUser -> ThreadUser
                        isReference -> ThreadReference
                        else -> ThreadSurface
                    },
                    RoundedCornerShape(if (isReference) 12.dp else 18.dp),
                )
                .padding(14.dp),
        ) {
            Text(
                message.content,
                color = if (isReference) ThreadMuted else ThreadText,
                style = if (isReference) {
                    MaterialTheme.typography.bodySmall
                } else {
                    MaterialTheme.typography.bodyLarge
                },
            )
            if (canCreateTask) {
                TextButton(onClick = { onCreateTask(message.id) }) {
                    Text("Create task", color = ThreadAccent)
                }
            }
            task?.let {
                Spacer(modifier = Modifier.padding(top = 4.dp))
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(Color(0x33111111), RoundedCornerShape(12.dp))
                        .clickable { onOpenTask(it.id) }
                        .padding(horizontal = 10.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        "Task · ${threadTaskLabel(it.status)}",
                        color = if (it.needsAttention) ThreadAttention else ThreadAccent,
                        style = MaterialTheme.typography.labelMedium,
                    )
                    Spacer(modifier = Modifier.weight(1f))
                    Text("View ›", color = ThreadMuted, style = MaterialTheme.typography.labelMedium)
                }
            }
        }
    }
}

/** Nothing costly runs until the user taps Create task here. */
@Composable
private fun ProposalCard(
    proposal: TaskProposal,
    enabled: Boolean,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 14.dp)
            .background(ThreadReference, RoundedCornerShape(14.dp))
            .padding(14.dp),
    ) {
        Text("Create a task?", color = ThreadText, fontWeight = FontWeight.Bold)
        Text(proposal.reason, color = ThreadMuted, style = MaterialTheme.typography.bodySmall)
        Text(
            proposal.suggestedGoal,
            color = ThreadText,
            style = MaterialTheme.typography.bodyMedium,
            maxLines = 3,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(top = 6.dp),
        )
        if (proposal.requiredCapabilities.isNotEmpty()) {
            Text(
                "Needs: ${proposal.requiredCapabilities.joinToString(", ")}",
                color = ThreadMuted,
                style = MaterialTheme.typography.labelSmall,
            )
        }
        Row(
            modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Button(
                onClick = onConfirm,
                enabled = enabled,
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF168C87)),
            ) { Text("Create task") }
            TextButton(onClick = onDismiss, enabled = enabled) {
                Text("Not now", color = ThreadMuted)
            }
        }
    }
}

private fun threadTaskLabel(status: String): String = status
    .replace('_', ' ')
    .replaceFirstChar { if (it.isLowerCase()) it.titlecase() else it.toString() }
