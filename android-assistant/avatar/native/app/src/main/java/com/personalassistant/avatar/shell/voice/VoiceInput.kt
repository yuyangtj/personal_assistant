package com.personalassistant.avatar.shell.voice

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.util.Log

/**
 * Push-to-talk English speech recognition. Prefers the on-device recognizer and falls back to
 * the system recognition service when no on-device English model is available.
 * All methods must be called on the main thread.
 */
class VoiceInput(private val context: Context, private val listener: Listener) {
    interface Listener {
        fun onListening()
        fun onPartialTranscript(text: String)
        fun onFinalTranscript(text: String)
        fun onVoiceLevel(level: Float)
        fun onVoiceError(message: String)
    }

    private var recognizer: SpeechRecognizer? = null
    private var usingOnDevice = false
    private var triedFallback = false

    val isAvailable: Boolean
        get() = SpeechRecognizer.isRecognitionAvailable(context) ||
            (Build.VERSION.SDK_INT >= 31 && SpeechRecognizer.isOnDeviceRecognitionAvailable(context))

    fun start() {
        triedFallback = false
        begin(preferOnDevice = true)
    }

    /** Stops capturing and delivers whatever was heard so far. */
    fun stop() {
        recognizer?.stopListening()
    }

    fun cancel() {
        recognizer?.cancel()
        release()
    }

    private fun begin(preferOnDevice: Boolean) {
        release()
        val created = if (
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
            preferOnDevice &&
            SpeechRecognizer.isOnDeviceRecognitionAvailable(context)
        ) {
            usingOnDevice = true
            SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
        } else {
            usingOnDevice = false
            SpeechRecognizer.createSpeechRecognizer(context)
        }
        recognizer = created
        created.setRecognitionListener(Callbacks())
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
            .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
        created.startListening(intent)
        Log.i(TAG, "VOICE_START: onDevice=$usingOnDevice")
    }

    private fun release() {
        recognizer?.destroy()
        recognizer = null
    }

    private inner class Callbacks : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) = listener.onListening()

        override fun onBeginningOfSpeech() = Unit

        override fun onRmsChanged(rmsdB: Float) {
            // Roughly -2 dB (silence) to 10 dB (loud speech).
            listener.onVoiceLevel(((rmsdB + 2f) / 12f).coerceIn(0f, 1f))
        }

        override fun onBufferReceived(buffer: ByteArray?) = Unit

        override fun onEndOfSpeech() = Unit

        override fun onPartialResults(partialResults: Bundle?) {
            firstResult(partialResults)?.let(listener::onPartialTranscript)
        }

        override fun onResults(results: Bundle?) {
            val text = firstResult(results).orEmpty()
            Log.i(TAG, "VOICE_RESULT: chars=${text.length} onDevice=$usingOnDevice")
            release()
            if (text.isBlank()) listener.onVoiceError("I didn't catch that. Tap the mic to try again.")
            else listener.onFinalTranscript(text)
        }

        override fun onError(error: Int) {
            Log.w(TAG, "VOICE_ERROR: code=$error onDevice=$usingOnDevice")
            val modelMissing = error == SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED ||
                error == SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE ||
                error == SpeechRecognizer.ERROR_CLIENT && usingOnDevice
            if (usingOnDevice && modelMissing && !triedFallback) {
                triedFallback = true
                begin(preferOnDevice = false)
                return
            }
            release()
            listener.onVoiceError(
                when (error) {
                    SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT ->
                        "I didn't catch that. Tap the mic to try again."
                    SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS ->
                        "Microphone permission is needed for voice requests."
                    SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT ->
                        "Speech recognition needs a network connection right now."
                    else -> "Voice input isn't available right now."
                },
            )
        }

        override fun onEvent(eventType: Int, params: Bundle?) = Unit
    }

    private fun firstResult(bundle: Bundle?): String? =
        bundle?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.trim()

    private companion object {
        const val TAG = "MiloVoice"
    }
}
