package com.personalassistant.avatar.shell

import android.os.Bundle
import android.util.Log
import android.view.ViewGroup
import android.widget.FrameLayout
import androidx.compose.foundation.background
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.setViewTreeLifecycleOwner
import com.personalassistant.avatar.MiloAssistantBridge
import com.unity3d.player.UnityPlayerActivity
import org.json.JSONObject

class MainActivity : UnityPlayerActivity(), LifecycleOwner {
    private val lifecycleRegistry = LifecycleRegistry(this)

    override val lifecycle: Lifecycle
        get() = lifecycleRegistry

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_CREATE)

        val composeOverlay = ComposeView(this).apply {
            setViewTreeLifecycleOwner(this@MainActivity)
            setContent {
                MaterialTheme {
                    AssistantControllerOverlay(::deliverMockResponse)
                }
            }
        }
        mUnityPlayer.frameLayout.addView(
            composeOverlay,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            ),
        )
        Log.i(TAG, "NATIVE_SHELL_READY")
    }

    override fun onStart() {
        super.onStart()
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_START)
    }

    override fun onResume() {
        super.onResume()
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_RESUME)
    }

    override fun onPause() {
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_PAUSE)
        super.onPause()
    }

    override fun onStop() {
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_STOP)
        super.onStop()
    }

    override fun onDestroy() {
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_DESTROY)
        super.onDestroy()
    }

    private fun deliverMockResponse(prompt: String): String {
        val responseId = "native-${System.currentTimeMillis()}"
        val responseText = if (prompt.equals("hello", ignoreCase = true)) {
            "Hello! The native Android controller is connected."
        } else {
            "I heard $prompt. The native controller is connected."
        }
        val responseJson = JSONObject()
            .put("responseId", responseId)
            .put("text", responseText)
            .put("emotion", "Warm")
            .put("intensity", 0.68)
            .toString()
        MiloAssistantBridge.deliverResponse(responseJson)
        Log.i(TAG, "NATIVE_RESPONSE_SENT: id=$responseId, promptChars=${prompt.length}")
        return responseText
    }

    private companion object {
        const val TAG = "MiloNative"
    }
}

@Composable
private fun AssistantControllerOverlay(onSend: (String) -> String) {
    var prompt by remember { mutableStateOf("") }
    var status by remember { mutableStateOf("Native controller ready") }
    val focusManager = LocalFocusManager.current
    val keyboardController = LocalSoftwareKeyboardController.current

    fun send() {
        val normalized = prompt.trim().take(160)
        if (normalized.isEmpty()) return
        status = onSend(normalized)
        prompt = ""
        focusManager.clearFocus()
        keyboardController?.hide()
    }

    Box(modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .background(Color(0xF20A171C), RoundedCornerShape(topStart = 28.dp, topEnd = 28.dp))
                .navigationBarsPadding()
                .imePadding()
                .padding(horizontal = 22.dp, vertical = 18.dp),
        ) {
            Text(
                text = "ASK MILO",
                color = Color(0xFF6CE7D5),
                fontWeight = FontWeight.Bold,
                style = MaterialTheme.typography.labelMedium,
            )
            Spacer(modifier = Modifier.height(5.dp))
            Text(
                text = status,
                color = Color(0xFFD7E8EA),
                maxLines = 2,
                style = MaterialTheme.typography.bodyMedium,
            )
            Spacer(modifier = Modifier.height(12.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                OutlinedTextField(
                    value = prompt,
                    onValueChange = { prompt = it.take(160) },
                    modifier = Modifier.weight(1f),
                    placeholder = { Text("Type a request…") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(onSend = { send() }),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedTextColor = Color.White,
                        unfocusedTextColor = Color.White,
                        focusedBorderColor = Color(0xFF49D9C6),
                        unfocusedBorderColor = Color(0xFF496067),
                        focusedPlaceholderColor = Color(0xFF8FA7AB),
                        unfocusedPlaceholderColor = Color(0xFF8FA7AB),
                        cursorColor = Color(0xFF49D9C6),
                    ),
                )
                Button(
                    onClick = ::send,
                    enabled = prompt.isNotBlank(),
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF168C87)),
                ) {
                    Text("Send", fontWeight = FontWeight.Bold)
                }
            }
        }
    }
}
