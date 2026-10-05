package com.personalassistant.shell

import android.Manifest
import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.view.ViewGroup
import android.webkit.HttpAuthHandler
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import androidx.webkit.WebSettingsCompat
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature

/**
 * The phone app: the assistant's web console, plus what only the phone can do (voice,
 * speech, alarms and timers, on-device answers) offered to the console through
 * [ConsoleBridge]. Every console feature reaches the phone with each server deploy.
 */
class MainActivity : ComponentActivity() {
    private lateinit var web: WebView
    private lateinit var voice: VoiceInput
    private lateinit var bridge: ConsoleBridge
    private lateinit var credentials: CredentialStore
    private lateinit var server: String
    private var authAttempts = 0

    /** The page's pending <input type="file">, answered when the picker returns. */
    private var fileChooser: ValueCallback<Array<Uri>>? = null
    private val pickFile = registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        fileChooser?.onReceiveValue(uri?.let { arrayOf(it) } ?: emptyArray())
        fileChooser = null
    }

    private val microphonePermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) voice.start()
        else bridge.onError("Microphone permission is needed for voice requests.")
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        server = preferences().getString(KEY_SERVER, null) ?: BuildConfig.CONSOLE_URL
        credentials = CredentialStore(this)
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)

        web = WebView(this).apply {
            setBackgroundColor(Color.parseColor("#071116"))
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            webViewClient = ConsoleClient()
            // Upload buttons in replies use the system file picker; the server checks the file.
            webChromeClient = object : WebChromeClient() {
                override fun onShowFileChooser(
                    view: WebView,
                    callback: ValueCallback<Array<Uri>>,
                    params: FileChooserParams,
                ): Boolean {
                    fileChooser?.onReceiveValue(null)
                    fileChooser = callback
                    return try {
                        pickFile.launch("*/*")
                        true
                    } catch (_: ActivityNotFoundException) {
                        fileChooser = null
                        false
                    }
                }
            }
        }
        // Passkeys in the console (approve with a fingerprint), for the sites this app is
        // linked to through /.well-known/assetlinks.json.
        if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_AUTHENTICATION)) {
            WebSettingsCompat.setWebAuthenticationSupport(
                web.settings, WebSettingsCompat.WEB_AUTHENTICATION_SUPPORT_FOR_APP,
            )
        }
        // A WebView ignores its own padding, so a frame around it keeps the console clear
        // of the status and navigation bars and the keyboard (edge-to-edge on 35+).
        val frame = FrameLayout(this).apply {
            setBackgroundColor(Color.parseColor("#071116"))
            addView(web, FrameLayout.LayoutParams(MATCH, MATCH))
        }
        setContentView(frame, ViewGroup.LayoutParams(MATCH, MATCH))
        ViewCompat.setOnApplyWindowInsetsListener(frame) { view, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime(),
            )
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }

        bridge = ConsoleBridge(
            scope = lifecycleScope,
            router = OnDeviceRouter(),
            speech = SpeechOutput(this),
            actions = PhoneActionRunner(this),
            voiceAvailable = { voice.isAvailable },
            startListening = ::startListening,
            stopListening = { voice.stop() },
        )
        voice = VoiceInput(this, bridge)
        val origin = ConsoleUrls.origin(server)
        if (origin != null && WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            WebViewCompat.addWebMessageListener(web, BRIDGE_NAME, setOf(origin), bridge)
        }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.canGoBack()) web.goBack() else finish()
            }
        })

        if (savedInstanceState == null || web.restoreState(savedInstanceState) == null) {
            web.loadUrl(ConsoleUrls.startUrl(server, intent?.dataString))
        }
    }

    /** A push notification or link into the console opens it here. */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val link = intent.dataString ?: return
        if (ConsoleUrls.sameOrigin(server, link)) web.loadUrl(link)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    override fun onPause() {
        voice.cancel()
        super.onPause()
    }

    override fun onDestroy() {
        bridge.close()
        web.destroy()
        super.onDestroy()
    }

    private fun startListening() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            voice.start()
        } else {
            microphonePermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun preferences() = getSharedPreferences("shell", Context.MODE_PRIVATE)

    private inner class ConsoleClient : WebViewClient() {
        /** Console pages stay in the app; everything else (GitHub, sources) opens outside. */
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val url = request.url.toString()
            if (ConsoleUrls.sameOrigin(server, url)) return false
            try {
                startActivity(Intent(Intent.ACTION_VIEW, request.url))
            } catch (_: ActivityNotFoundException) {
                // Nothing on the phone can open it; stay on the console.
            }
            return true
        }

        override fun onPageFinished(view: WebView, url: String) {
            authAttempts = 0
        }

        /** The console's login (Caddy basic auth): remembered, encrypted, on this phone. */
        override fun onReceivedHttpAuthRequest(
            view: WebView,
            handler: HttpAuthHandler,
            host: String,
            realm: String?,
        ) {
            val saved = credentials.load(host)
            if (saved != null && authAttempts == 0) {
                authAttempts++
                handler.proceed(saved.first, saved.second)
                return
            }
            if (saved != null) credentials.clear(host)  // the saved login was refused
            askForLogin(host, handler)
        }

        override fun onReceivedError(
            view: WebView,
            request: WebResourceRequest,
            error: WebResourceError,
        ) {
            if (request.isForMainFrame) showUnreachable()
        }
    }

    private fun askForLogin(host: String, handler: HttpAuthHandler) {
        val user = EditText(this).apply { hint = "User"; isSingleLine = true }
        val password = EditText(this).apply {
            hint = "Password"
            isSingleLine = true
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        val form = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(PADDING, PADDING / 2, PADDING, 0)
            addView(TextView(context).apply { text = "Saved on this phone, encrypted." })
            addView(user)
            addView(password)
        }
        AlertDialog.Builder(this)
            .setTitle("Sign in to $host")
            .setView(form)
            .setCancelable(false)
            .setPositiveButton("Sign in") { _, _ ->
                val name = user.text.toString().trim()
                val secret = password.text.toString()
                credentials.save(host, name, secret)
                authAttempts = 1
                handler.proceed(name, secret)
            }
            .setNegativeButton("Cancel") { _, _ -> handler.cancel() }
            .show()
    }

    private fun showUnreachable() {
        AlertDialog.Builder(this)
            .setTitle("Can't reach the assistant")
            .setMessage(server)
            .setPositiveButton("Retry") { _, _ -> web.reload() }
            .setNeutralButton("Change server") { _, _ -> askForServer() }
            .show()
    }

    private fun askForServer() {
        val field = EditText(this).apply {
            setText(server)
            isSingleLine = true
            inputType = InputType.TYPE_TEXT_VARIATION_URI
        }
        AlertDialog.Builder(this)
            .setTitle("Assistant server")
            .setView(field)
            .setPositiveButton("Save") { _, _ ->
                val normalized = ConsoleUrls.normalize(field.text.toString()) ?: return@setPositiveButton
                preferences().edit().putString(KEY_SERVER, normalized).apply()
                recreate()  // the bridge is registered for one origin
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private companion object {
        const val BRIDGE_NAME = "AndroidAssistant"
        const val KEY_SERVER = "server"
        const val MATCH = ViewGroup.LayoutParams.MATCH_PARENT
        const val PADDING = 48
    }
}
