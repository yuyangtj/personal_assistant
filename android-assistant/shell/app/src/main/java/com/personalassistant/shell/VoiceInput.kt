package com.personalassistant.shell

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognitionSupport
import android.speech.RecognitionSupportCallback
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.util.Log
import androidx.annotation.RequiresApi
import androidx.core.content.ContextCompat
import java.util.Locale

/** Push-to-talk transcription, preferring Android's on-device recognizer. */
class VoiceInput(private val context: Context, private val listener: Listener) {
    private companion object {
        const val TAG = "VoiceInput"
    }

    interface Listener {
        fun onListening()
        fun onPartialTranscript(text: String)
        fun onFinalTranscript(text: String)
        fun onVoiceLevel(level: Float)
        fun onError(message: String)
    }

    private var recognizer: SpeechRecognizer? = null
    private var usingOnDevice = false
    private var triedFallback = false
    private var language = Locale.getDefault().toLanguageTag()
    /** The on-device language found installed, so later taps start at once. */
    private var onDeviceLanguage: String? = null

    val isAvailable: Boolean
        get() = SpeechRecognizer.isRecognitionAvailable(context) ||
            (Build.VERSION.SDK_INT >= 31 && SpeechRecognizer.isOnDeviceRecognitionAvailable(context))

    fun start() {
        triedFallback = false
        spare?.destroy()
        spare = null
        val onDevice = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
            SpeechRecognizer.isOnDeviceRecognitionAvailable(context)
        val known = onDeviceLanguage
        when {
            !onDevice -> begin(onDevice = false, Locale.getDefault().toLanguageTag())
            known != null -> begin(onDevice = true, known)
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU -> chooseOnDeviceLanguage()
            else -> begin(onDevice = true, Locale.getDefault().toLanguageTag())
        }
    }

    fun stop() = recognizer?.stopListening() ?: Unit

    fun cancel() {
        recognizer?.cancel()
        release()
    }

    /**
     * Ask the on-device recognizer which languages it has, and use the closest one to the
     * phone's. Without one installed this time goes online, and the model is requested so
     * the next time stays on the phone.
     */
    @RequiresApi(Build.VERSION_CODES.TIRAMISU)
    private fun chooseOnDeviceLanguage() {
        release()
        val wanted = Locale.getDefault()
        val checker = SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
        recognizer = checker
        checker.checkRecognitionSupport(
            intent(wanted.toLanguageTag()),
            ContextCompat.getMainExecutor(context),
            object : RecognitionSupportCallback {
                override fun onSupportResult(support: RecognitionSupport) {
                    if (recognizer !== checker) return  // cancelled meanwhile
                    val installed = SpeechLanguage.choose(wanted, support.installedOnDeviceLanguages)
                    if (installed != null) {
                        onDeviceLanguage = installed
                        // Listen on this same recognizer: destroying it now would cancel
                        // the session that starts next on the same service.
                        listen(checker, onDevice = true, installed)
                        return
                    }
                    val pending = support.pendingOnDeviceLanguages
                    val downloadable = SpeechLanguage.choose(
                        wanted, support.supportedOnDeviceLanguages + pending,
                    )
                    if (downloadable != null && downloadable !in pending) {
                        Log.i(TAG, "Downloading the on-device speech model for $downloadable")
                        checker.triggerModelDownload(intent(downloadable))
                    }
                    // Kept until the next start, so the download request isn't cancelled.
                    spare = checker
                    recognizer = null
                    begin(onDevice = false, downloadable ?: wanted.toLanguageTag())
                }

                override fun onError(error: Int) {
                    if (recognizer === checker) begin(onDevice = false, wanted.toLanguageTag())
                }
            },
        )
    }

    private fun begin(onDevice: Boolean, language: String) {
        release()
        val phone = onDevice && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S
        val created = if (phone) {
            SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
        } else {
            SpeechRecognizer.createSpeechRecognizer(context)
        }
        listen(created, phone, language)
    }

    private fun listen(created: SpeechRecognizer, onDevice: Boolean, language: String) {
        this.language = language
        lastPartial = ""
        usingOnDevice = onDevice
        Log.i(TAG, "Listening ${if (onDevice) "on the phone" else "online"} in $language")
        recognizer = created
        created.setRecognitionListener(Callbacks())
        created.startListening(intent(language))
    }

    private fun intent(language: String) =
        Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE, language)
            .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            // Finish sooner after the speaker stops (the default waits about two
            // seconds of silence); tapping the mic again finishes at once.
            .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 800L)
            .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 800L)

    private fun release() {
        recognizer?.destroy()
        recognizer = null
    }

    /** What was heard so far; the on-device recognizer's final result is sometimes empty. */
    private var lastPartial = ""

    /** A language checker kept alive while its model download request is handed over. */
    private var spare: SpeechRecognizer? = null

    private inner class Callbacks : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) = listener.onListening()
        override fun onBeginningOfSpeech() = Unit
        override fun onBufferReceived(buffer: ByteArray?) = Unit
        override fun onEndOfSpeech() = Unit
        override fun onEvent(eventType: Int, params: Bundle?) = Unit

        override fun onRmsChanged(rmsdB: Float) {
            listener.onVoiceLevel(((rmsdB + 2f) / 12f).coerceIn(0f, 1f))
        }

        override fun onPartialResults(partialResults: Bundle?) {
            firstResult(partialResults)?.takeIf { it.isNotBlank() }?.let {
                lastPartial = it
                listener.onPartialTranscript(it)
            }
        }

        override fun onResults(results: Bundle?) {
            val text = firstResult(results)?.takeIf { it.isNotBlank() } ?: lastPartial
            release()
            if (text.isBlank()) listener.onError("I didn’t catch that. Tap the circle to try again.")
            else listener.onFinalTranscript(text)
        }

        override fun onError(error: Int) {
            Log.i(TAG, "Recognition error $error (${if (usingOnDevice) "on the phone" else "online"})")
            if (lastPartial.isNotBlank()) {
                // Something was heard before the error: keep it rather than lose it.
                val heard = lastPartial
                release()
                listener.onFinalTranscript(heard)
                return
            }
            val missingModel = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
                (error == SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED ||
                    error == SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE) ||
                error == SpeechRecognizer.ERROR_CLIENT && usingOnDevice
            if (usingOnDevice && missingModel && !triedFallback) {
                triedFallback = true
                onDeviceLanguage = null  // the model went away; look again next time
                begin(onDevice = false, language)
                return
            }
            release()
            listener.onError(
                when (error) {
                    SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT ->
                        "I didn’t catch that. Tap the circle to try again."
                    SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS ->
                        "Microphone permission is needed for voice requests."
                    SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT ->
                        "Speech recognition needs a network connection right now."
                    else -> "Voice input isn’t available right now."
                },
            )
        }
    }

    private fun firstResult(bundle: Bundle?): String? =
        bundle?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.trim()
}
