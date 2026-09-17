package com.personalassistant.avatar.shell

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.view.ViewGroup
import android.widget.FrameLayout
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.platform.ComposeView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.setViewTreeLifecycleOwner
import androidx.savedstate.SavedStateRegistry
import androidx.savedstate.SavedStateRegistryController
import androidx.savedstate.SavedStateRegistryOwner
import androidx.savedstate.setViewTreeSavedStateRegistryOwner
import com.personalassistant.avatar.MiloHost
import com.personalassistant.avatar.shell.assistant.ApprovalTokenStore
import com.personalassistant.avatar.shell.assistant.AssistantSession
import com.personalassistant.avatar.shell.assistant.PhoneActionRunner
import com.personalassistant.avatar.shell.avatar.AvatarBridge
import com.personalassistant.avatar.shell.ui.AssistantScreen
import com.personalassistant.avatar.shell.voice.VoiceInput
import com.unity3d.player.UnityPlayerActivity

/** Hosts the Unity avatar and overlays the native assistant controls. */
class MainActivity : UnityPlayerActivity(), LifecycleOwner, SavedStateRegistryOwner {
    private val lifecycleRegistry = LifecycleRegistry(this)
    private val savedStateController = SavedStateRegistryController.create(this)
    private lateinit var session: AssistantSession
    private lateinit var voice: VoiceInput

    override val lifecycle: Lifecycle
        get() = lifecycleRegistry

    override val savedStateRegistry: SavedStateRegistry
        get() = savedStateController.savedStateRegistry

    override fun onCreate(savedInstanceState: Bundle?) {
        // Must be set before Unity starts so its prototype HUD stays hidden.
        MiloHost.setEmbedded(true)
        savedStateController.performAttach()
        savedStateController.performRestore(savedInstanceState)
        super.onCreate(savedInstanceState)
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_CREATE)
        // Compose resolves its owners from the window's root view, above Unity's own views.
        window.decorView.setViewTreeLifecycleOwner(this)
        window.decorView.setViewTreeSavedStateRegistryOwner(this)

        session = AssistantSession(
            scope = lifecycleScope,
            avatar = AvatarBridge(),
            preferences = getSharedPreferences("assistant", Context.MODE_PRIVATE),
            speechCacheDirectory = cacheDir,
            approvalTokens = ApprovalTokenStore(
                getSharedPreferences("assistant_secure", Context.MODE_PRIVATE),
            ),
            runAction = PhoneActionRunner(this)::run,
        )
        voice = VoiceInput(this, object : VoiceInput.Listener {
            override fun onListening() = session.onListening()
            override fun onPartialTranscript(text: String) = session.onPartialTranscript(text)
            override fun onFinalTranscript(text: String) = session.onFinalTranscript(text)
            override fun onVoiceLevel(level: Float) = session.onVoiceLevel(level)
            override fun onVoiceError(message: String) = session.onVoiceError(message)
        })
        MiloHost.setListener(object : MiloHost.Listener {
            override fun onSpeechStarted(responseId: String) {
                runOnUiThread { session.onSpeechStarted(responseId) }
            }

            override fun onSpeechFinished(responseId: String, reason: String) {
                runOnUiThread { session.onSpeechFinished(responseId, reason) }
            }
        })

        val overlay = ComposeView(this).apply {
            setViewTreeLifecycleOwner(this@MainActivity)
            setViewTreeSavedStateRegistryOwner(this@MainActivity)
            setContent {
                val state by session.uiState.collectAsState()
                MaterialTheme {
                    AssistantScreen(
                        state = state,
                        onSend = session::send,
                        onCancel = session::cancel,
                        onBackendUrl = session::updateBackendUrl,
                        onCharacter = session::selectCharacter,
                        onTestConnection = session::checkConnection,
                        voiceAvailable = voice.isAvailable,
                        onMicrophone = ::toggleListening,
                        onConfirmAction = session::confirmAction,
                        onDismissAction = session::dismissAction,
                        onOpenPullRequest = ::openPullRequest,
                        onApprovePullRequest = session::approvePullRequest,
                        onRejectPullRequest = session::rejectPullRequest,
                        onApprovalToken = session::updateApprovalToken,
                    )
                }
            }
        }
        mUnityPlayer.frameLayout.addView(
            overlay,
            FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT),
        )
        handleDebugPrompt(intent)
        Log.i(TAG, "NATIVE_SHELL_READY")
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        savedStateController.performSave(outState)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleDebugPrompt(intent)
    }

    private fun toggleListening() {
        if (session.uiState.value.activity == com.personalassistant.avatar.shell.assistant.Activity.LISTENING) {
            voice.stop()
            return
        }
        if (session.uiState.value.isBusy) return
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            voice.start()
        } else {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQUEST_MICROPHONE)
        }
    }

    private fun openPullRequest(url: String) {
        val uri = Uri.parse(url)
        if (uri.scheme != "https" || !uri.host.equals("github.com", ignoreCase = true)) {
            session.onVoiceError("The pull request link is invalid.")
            return
        }
        runCatching { startActivity(Intent(Intent.ACTION_VIEW, uri)) }
            .onFailure { session.onVoiceError("No browser can open the pull request.") }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQUEST_MICROPHONE) return
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) voice.start()
        else session.onVoiceError("Microphone permission is needed for voice requests.")
    }

    /**
     * Debug builds accept `adb shell am start ... --es prompt 'text'` to send a request and
     * `--ez listen true` to open the microphone, for automated device checks.
     */
    private fun handleDebugPrompt(intent: Intent?) {
        if (!BuildConfig.DEBUG || intent == null) return
        intent.getStringExtra(EXTRA_PROMPT)?.let { prompt ->
            intent.removeExtra(EXTRA_PROMPT)
            window.decorView.postDelayed({ session.send(prompt) }, 1_500)
        }
        if (intent.getBooleanExtra(EXTRA_LISTEN, false)) {
            intent.removeExtra(EXTRA_LISTEN)
            window.decorView.postDelayed(::toggleListening, 1_500)
        }
    }

    override fun onStart() {
        super.onStart()
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_START)
    }

    override fun onResume() {
        super.onResume()
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_RESUME)
        session.checkConnection()
    }

    override fun onPause() {
        voice.cancel()
        session.onVoiceError("")
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_PAUSE)
        super.onPause()
    }

    override fun onStop() {
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_STOP)
        super.onStop()
    }

    override fun onDestroy() {
        MiloHost.setListener(null)
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_DESTROY)
        super.onDestroy()
    }

    private companion object {
        const val TAG = "MiloNative"
        const val EXTRA_PROMPT = "prompt"
        const val EXTRA_LISTEN = "listen"
        const val REQUEST_MICROPHONE = 41
    }
}
