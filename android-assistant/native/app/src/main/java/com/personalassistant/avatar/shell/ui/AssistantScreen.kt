package com.personalassistant.avatar.shell.ui

import androidx.compose.foundation.Canvas
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
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.personalassistant.avatar.shell.assistant.Activity
import com.personalassistant.avatar.shell.assistant.Connection
import com.personalassistant.avatar.shell.assistant.PendingPullRequestApproval
import com.personalassistant.avatar.shell.assistant.UiState
import com.personalassistant.avatar.shell.avatar.AvatarCharacter

private val Panel = Color(0xE60A171C)
private val Accent = Color(0xFF49D9C6)
private val Muted = Color(0xFF9FB4B8)
private val TextPrimary = Color(0xFFE6F1F2)

@Composable
fun AssistantScreen(
    state: UiState,
    onSend: (String) -> Unit,
    onCancel: () -> Unit,
    onBackendUrl: (String) -> Unit,
    onCharacter: (AvatarCharacter) -> Unit,
    onTestConnection: () -> Unit,
    voiceAvailable: Boolean,
    onMicrophone: () -> Unit,
    onConfirmAction: () -> Unit,
    onDismissAction: () -> Unit,
    onOpenPullRequest: (String) -> Unit,
    onApprovePullRequest: () -> Unit,
    onRejectPullRequest: () -> Unit,
    onApprovalToken: (String) -> Unit,
) {
    var showSettings by remember { mutableStateOf(false) }

    Box(modifier = Modifier.fillMaxSize()) {
        StatusBar(
            state = state,
            onSettings = { showSettings = true },
            modifier = Modifier.align(Alignment.TopCenter),
        )
        ConversationPanel(
            state = state,
            onSend = onSend,
            onCancel = onCancel,
            voiceAvailable = voiceAvailable,
            onMicrophone = onMicrophone,
            onConfirmAction = onConfirmAction,
            onDismissAction = onDismissAction,
            onOpenPullRequest = onOpenPullRequest,
            onApprovePullRequest = onApprovePullRequest,
            onRejectPullRequest = onRejectPullRequest,
            modifier = Modifier.align(Alignment.BottomCenter),
        )
    }

    if (showSettings) {
        SettingsDialog(
            state = state,
            onDismiss = { showSettings = false },
            onBackendUrl = onBackendUrl,
            onCharacter = onCharacter,
            onTestConnection = onTestConnection,
            onApprovalToken = onApprovalToken,
        )
    }
}

@Composable
private fun StatusBar(state: UiState, onSettings: () -> Unit, modifier: Modifier = Modifier) {
    val (label, color) = statusLabel(state)
    Row(
        modifier = modifier
            .statusBarsPadding()
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Row(
            modifier = Modifier
                .background(Panel, RoundedCornerShape(20.dp))
                .padding(horizontal = 14.dp, vertical = 9.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(modifier = Modifier.size(10.dp).background(color, CircleShape))
            Spacer(modifier = Modifier.size(8.dp))
            Text(state.character.label, color = TextPrimary, fontWeight = FontWeight.Bold)
            Text("  ·  $label", color = Muted)
        }
        Spacer(modifier = Modifier.weight(1f))
        TextButton(
            onClick = onSettings,
            modifier = Modifier.background(Panel, RoundedCornerShape(20.dp)),
        ) {
            Text("Settings", color = Accent, fontWeight = FontWeight.Bold)
        }
    }
}

private fun statusLabel(state: UiState): Pair<String, Color> = when {
    state.activity == Activity.LISTENING -> "Listening" to Color(0xFF6FC3FF)
    state.activity == Activity.SENDING -> "Sending" to Color(0xFF6FC3FF)
    state.activity == Activity.THINKING -> "Thinking" to Color(0xFFB48CFF)
    state.activity == Activity.WAITING_FOR_APPROVAL -> "Review needed" to Color(0xFFFFC857)
    state.activity == Activity.SPEAKING -> "Speaking" to Accent
    state.activity == Activity.DONE -> "Done" to Color(0xFF6BE38A)
    state.activity == Activity.FAILED -> "Problem" to Color(0xFFFF7A70)
    state.activity == Activity.CANCELLED -> "Cancelled" to Muted
    state.connection == Connection.CHECKING -> "Connecting" to Muted
    state.connection == Connection.OFFLINE -> "Offline" to Color(0xFFFF7A70)
    else -> "Online" to Color(0xFF6BE38A)
}

@Composable
private fun ConversationPanel(
    state: UiState,
    onSend: (String) -> Unit,
    onCancel: () -> Unit,
    voiceAvailable: Boolean,
    onMicrophone: () -> Unit,
    onConfirmAction: () -> Unit,
    onDismissAction: () -> Unit,
    onOpenPullRequest: (String) -> Unit,
    onApprovePullRequest: () -> Unit,
    onRejectPullRequest: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var prompt by remember { mutableStateOf("") }
    val listening = state.activity == Activity.LISTENING
    val focusManager = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current

    fun submit() {
        if (prompt.isBlank() || state.isBusy) return
        onSend(prompt)
        prompt = ""
        focusManager.clearFocus()
        keyboard?.hide()
    }

    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(Panel, RoundedCornerShape(topStart = 28.dp, topEnd = 28.dp))
            .navigationBarsPadding()
            .imePadding()
            .padding(horizontal = 20.dp, vertical = 16.dp),
    ) {
        state.lastRequest?.let {
            Text("YOU", color = Muted, style = MaterialTheme.typography.labelSmall)
            Text(it, color = TextPrimary, maxLines = 2, style = MaterialTheme.typography.bodyMedium)
            Spacer(modifier = Modifier.height(8.dp))
        }
        Text(state.character.label.uppercase(), color = Accent, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.labelSmall)
        Text(
            text = state.reply ?: placeholderReply(state),
            color = TextPrimary,
            maxLines = 6,
            overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
            style = MaterialTheme.typography.bodyLarge,
        )
        state.pendingAction?.let { action ->
            Spacer(modifier = Modifier.height(10.dp))
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color(0xFF15343A), RoundedCornerShape(16.dp))
                    .border(1.dp, Accent, RoundedCornerShape(16.dp))
                    .padding(14.dp),
            ) {
                Text("CONFIRM ACTION", color = Accent, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.labelSmall)
                Text(action.summary, color = TextPrimary, style = MaterialTheme.typography.titleMedium)
                Spacer(modifier = Modifier.height(10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Button(
                        onClick = onConfirmAction,
                        colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF168C87)),
                    ) { Text("Confirm", fontWeight = FontWeight.Bold) }
                    OutlinedButton(onClick = onDismissAction) { Text("Not now", color = Muted) }
                }
            }
        }
        state.pendingApproval?.let { approval ->
            Spacer(modifier = Modifier.height(10.dp))
            PullRequestApprovalCard(
                approval = approval,
                tokenConfigured = state.hasApprovalToken,
                inFlight = state.approvalInFlight,
                onOpen = { onOpenPullRequest(approval.url) },
                onApprove = onApprovePullRequest,
                onReject = onRejectPullRequest,
            )
        }
        state.hint?.takeIf { it.isNotBlank() }?.let {
            Spacer(modifier = Modifier.height(4.dp))
            Text(it, color = Color(0xFFFFC38A), style = MaterialTheme.typography.bodySmall)
        }
        Spacer(modifier = Modifier.height(12.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = if (listening) state.transcript.orEmpty() else prompt,
                onValueChange = { if (!listening) prompt = it.take(2_000) },
                modifier = Modifier.weight(1f),
                readOnly = listening,
                placeholder = { Text(if (listening) "Listening…" else "Ask your assistant…") },
                maxLines = 3,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { submit() }),
                colors = fieldColors(),
            )
            if (voiceAvailable && (prompt.isBlank() || listening) && !state.isBusy) {
                MicrophoneButton(listening = listening, level = state.voiceLevel, onClick = onMicrophone)
            } else if (state.activity == Activity.THINKING || state.activity == Activity.SENDING) {
                OutlinedButton(onClick = onCancel) { Text("Cancel", color = Color(0xFFFF9C94)) }
            } else {
                Button(
                    onClick = ::submit,
                    enabled = prompt.isNotBlank() && !state.isBusy,
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF168C87)),
                ) {
                    Text("Send", fontWeight = FontWeight.Bold)
                }
            }
        }
        state.character.credit?.let {
            Spacer(modifier = Modifier.height(8.dp))
            Text(it, color = Muted, style = MaterialTheme.typography.labelSmall, modifier = Modifier.align(Alignment.CenterHorizontally))
        }
    }
}

@Composable
private fun PullRequestApprovalCard(
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
            .background(Color(0xFF172B3A), RoundedCornerShape(16.dp))
            .border(1.dp, Color(0xFF6FC3FF), RoundedCornerShape(16.dp))
            .padding(14.dp),
    ) {
        Text(
            "REVIEW REQUIRED",
            color = Color(0xFF6FC3FF),
            fontWeight = FontWeight.Bold,
            style = MaterialTheme.typography.labelSmall,
        )
        Text(
            "${approval.repository}  ·  PR #${approval.number}",
            color = TextPrimary,
            fontWeight = FontWeight.Bold,
            style = MaterialTheme.typography.titleMedium,
        )
        Text(
            "Commit ${approval.shortSha}  ·  ${if (approval.draft) "Draft" else "Ready"}",
            color = Muted,
            style = MaterialTheme.typography.bodySmall,
        )
        Spacer(modifier = Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = onOpen, enabled = !inFlight) {
                Text("Open GitHub", color = Color(0xFF6FC3FF))
            }
            Button(
                onClick = onApprove,
                enabled = tokenConfigured && !inFlight,
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF168C87)),
            ) {
                Text(if (inFlight) "Checking…" else "Approve", fontWeight = FontWeight.Bold)
            }
            OutlinedButton(onClick = onReject, enabled = tokenConfigured && !inFlight) {
                Text("Reject", color = Color(0xFFFF9C94))
            }
        }
        if (!tokenConfigured) {
            Text(
                "Add the approval token in Settings to enable decisions.",
                color = Color(0xFFFFC38A),
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun MicrophoneButton(listening: Boolean, level: Float, onClick: () -> Unit) {
    val ring = if (listening) 3.dp + (level * 7).dp else 1.dp
    Box(
        modifier = Modifier
            .size(56.dp)
            .background(if (listening) Color(0xFF168C87) else Color(0xFF1B3036), CircleShape)
            .border(ring, if (listening) Accent else Color(0xFF496067), CircleShape)
            .clickable(onClick = onClick)
            .semantics { contentDescription = if (listening) "Stop listening" else "Speak a request" },
        contentAlignment = Alignment.Center,
    ) {
        Canvas(modifier = Modifier.size(24.dp)) {
            val color = Color.White
            val w = size.width
            val h = size.height
            if (listening) {
                drawRoundRect(color, topLeft = Offset(w * 0.25f, h * 0.25f), size = Size(w * 0.5f, h * 0.5f), cornerRadius = CornerRadius(3f))
                return@Canvas
            }
            drawRoundRect(color, topLeft = Offset(w * 0.34f, 0f), size = Size(w * 0.32f, h * 0.62f), cornerRadius = CornerRadius(w * 0.16f))
            drawArc(
                color = color,
                startAngle = 0f,
                sweepAngle = 180f,
                useCenter = false,
                topLeft = Offset(w * 0.18f, h * 0.22f),
                size = Size(w * 0.64f, h * 0.56f),
                style = Stroke(width = w * 0.09f),
            )
            drawLine(color, Offset(w * 0.5f, h * 0.78f), Offset(w * 0.5f, h * 0.95f), strokeWidth = w * 0.09f)
        }
    }
}

private fun placeholderReply(state: UiState): String = if (state.activity == Activity.LISTENING) {
    "I'm listening…"
} else if (state.activity == Activity.SENDING || state.activity == Activity.THINKING) {
    "Working on it…"
} else when (state.connection) {
    Connection.OFFLINE -> "I can't reach the assistant server. Check Settings."
    Connection.CHECKING -> "Connecting to your assistant…"
    Connection.ONLINE -> "Hi! What can I do for you?"
}

@Composable
private fun SettingsDialog(
    state: UiState,
    onDismiss: () -> Unit,
    onBackendUrl: (String) -> Unit,
    onCharacter: (AvatarCharacter) -> Unit,
    onTestConnection: () -> Unit,
    onApprovalToken: (String) -> Unit,
) {
    var url by remember { mutableStateOf(state.backendUrl) }
    var approvalToken by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Assistant settings") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(
                    value = url,
                    onValueChange = { url = it },
                    label = { Text("Backend URL") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Done),
                )
                Text(
                    "Over USB run: adb reverse tcp:8010 tcp:8010",
                    style = MaterialTheme.typography.bodySmall,
                )
                Text("Connection: ${state.connection.name.lowercase()}", style = MaterialTheme.typography.bodySmall)
                OutlinedTextField(
                    value = approvalToken,
                    onValueChange = { approvalToken = it.take(512) },
                    label = { Text("Approval token") },
                    placeholder = {
                        Text(
                            if (state.hasApprovalToken) {
                                "Stored securely"
                            } else {
                                "Required for PR decisions"
                            },
                        )
                    },
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                )
                Text(
                    "Entered at runtime and encrypted with Android Keystore; never bundled in the APK.",
                    style = MaterialTheme.typography.bodySmall,
                )
                if (state.hasApprovalToken) {
                    TextButton(onClick = { onApprovalToken("") }) {
                        Text("Forget approval token", color = Color(0xFFFF9C94))
                    }
                }
                Text("Character", fontWeight = FontWeight.Bold)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    AvatarCharacter.entries.forEach { character ->
                        FilterChip(
                            selected = state.character == character,
                            onClick = { onCharacter(character) },
                            label = { Text(character.label) },
                        )
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = {
                onBackendUrl(url)
                if (approvalToken.isNotBlank()) onApprovalToken(approvalToken)
                onDismiss()
            }) { Text("Save") }
        },
        dismissButton = {
            TextButton(onClick = {
                onBackendUrl(url)
                onTestConnection()
            }) { Text("Test connection") }
        },
    )
}

@Composable
private fun fieldColors() = OutlinedTextFieldDefaults.colors(
    focusedTextColor = Color.White,
    unfocusedTextColor = Color.White,
    focusedBorderColor = Accent,
    unfocusedBorderColor = Color(0xFF496067),
    focusedPlaceholderColor = Muted,
    unfocusedPlaceholderColor = Muted,
    cursorColor = Accent,
)
