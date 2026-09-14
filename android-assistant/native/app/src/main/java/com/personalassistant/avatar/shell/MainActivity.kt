package com.personalassistant.avatar.shell

import android.content.Context
import android.content.Intent
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
import com.personalassistant.avatar.shell.assistant.AssistantSession
import com.personalassistant.avatar.shell.avatar.AvatarBridge
import com.personalassistant.avatar.shell.ui.AssistantScreen
import com.unity3d.player.UnityPlayerActivity

/** Hosts the Unity avatar and overlays the native assistant controls. */
class MainActivity : UnityPlayerActivity(), LifecycleOwner, SavedStateRegistryOwner {
    private val lifecycleRegistry = LifecycleRegistry(this)
    private val savedStateController = SavedStateRegistryController.create(this)
    private lateinit var session: AssistantSession

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
        )
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

    /** Debug builds accept `adb shell am start ... --es prompt "text"` for automated device checks. */
    private fun handleDebugPrompt(intent: Intent?) {
        if (!BuildConfig.DEBUG) return
        val prompt = intent?.getStringExtra(EXTRA_PROMPT) ?: return
        intent.removeExtra(EXTRA_PROMPT)
        window.decorView.postDelayed({ session.send(prompt) }, 1_500)
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
    }
}
