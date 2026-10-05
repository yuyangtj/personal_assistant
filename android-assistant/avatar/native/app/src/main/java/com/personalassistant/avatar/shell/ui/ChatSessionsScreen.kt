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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.personalassistant.avatar.shell.assistant.ChatCenterState
import com.personalassistant.avatar.shell.assistant.ChatSession
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private val ChatBackground = Color(0xFF071116)
private val ChatSurface = Color(0xFF102127)
private val ChatAccent = Color(0xFF49D9C6)
private val ChatText = Color(0xFFE6F1F2)
private val ChatMuted = Color(0xFF9FB4B8)
private val ChatDanger = Color(0xFFFF8E86)

@Composable
fun ChatSessionsScreen(
    state: ChatCenterState,
    activeChatSessionId: String?,
    onRefresh: () -> Unit,
    onNewChat: () -> Unit,
    onResumeChat: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .background(ChatBackground)
            .statusBarsPadding()
            .padding(bottom = 72.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text("Conversations", color = ChatText, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                Text("Start fresh or continue where you stopped", color = ChatMuted, style = MaterialTheme.typography.bodySmall)
            }
            TextButton(onClick = onRefresh, enabled = !state.loading) {
                Text("Refresh", color = ChatAccent)
            }
        }

        Button(
            onClick = onNewChat,
            enabled = !state.loading,
            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF168C87)),
            modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp),
        ) {
            Text("New conversation", fontWeight = FontWeight.Bold)
        }

        state.error?.let {
            Text(
                it,
                color = ChatDanger,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.fillMaxWidth().padding(20.dp).background(Color(0xFF3A2022), RoundedCornerShape(12.dp)).padding(12.dp),
            )
        }

        when {
            state.loading && state.sessions.isEmpty() -> {
                Column(
                    modifier = Modifier.fillMaxWidth().padding(36.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    CircularProgressIndicator(color = ChatAccent, modifier = Modifier.size(28.dp))
                    Spacer(modifier = Modifier.size(12.dp))
                    Text("Loading conversations…", color = ChatMuted)
                }
            }
            state.sessions.isEmpty() -> {
                Column(
                    modifier = Modifier.fillMaxWidth().padding(36.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text("No saved conversations", color = ChatText, fontWeight = FontWeight.Bold)
                    Text("Start one to keep future follow-ups together.", color = ChatMuted, style = MaterialTheme.typography.bodySmall)
                }
            }
            else -> LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(20.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                items(state.sessions, key = { it.id }) { chat ->
                    ChatCard(
                        chat = chat,
                        active = chat.id == activeChatSessionId,
                        onClick = { onResumeChat(chat.id) },
                    )
                }
            }
        }
    }
}

@Composable
private fun ChatCard(chat: ChatSession, active: Boolean, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(ChatSurface, RoundedCornerShape(18.dp))
            .border(
                width = if (active) 1.dp else 0.dp,
                color = if (active) ChatAccent else Color.Transparent,
                shape = RoundedCornerShape(18.dp),
            )
            .clickable(onClick = onClick)
            .padding(16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .size(11.dp)
                .background(if (active) ChatAccent else Color(0xFF557078), CircleShape),
        )
        Spacer(modifier = Modifier.size(12.dp))
        Column(modifier = Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    chat.title,
                    color = ChatText,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                if (active) {
                    Text("CURRENT", color = ChatAccent, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold)
                }
            }
            Text("Updated ${formatChatTime(chat.updatedAt)}", color = ChatMuted, style = MaterialTheme.typography.bodySmall)
        }
        Spacer(modifier = Modifier.size(8.dp))
        Text("›", color = ChatMuted, style = MaterialTheme.typography.headlineSmall)
    }
}

private fun formatChatTime(raw: String): String {
    if (raw.isBlank()) return ""
    return runCatching {
        OffsetDateTime.parse(raw)
            .atZoneSameInstant(ZoneId.systemDefault())
            .format(DateTimeFormatter.ofPattern("MMM d, HH:mm"))
    }.getOrDefault(raw.take(16).replace('T', ' '))
}
