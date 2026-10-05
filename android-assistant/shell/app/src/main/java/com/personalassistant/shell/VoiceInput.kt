package com.personalassistant.shell

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import java.util.Locale

/** Push-to-talk transcription, preferring Android's on-device recognizer. */
class VoiceInput(private val context: Context, private val listener: Listener) {
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

    val isAvailable: Boolean
        get() = SpeechRecognizer.isRecognitionAvailable(context) ||
            (Build.VERSION.SDK_INT >= 31 && SpeechRecognizer.isOnDeviceRecognitionAvailable(context))

    fun start() {
        triedFallback = false
        begin(preferOnDevice = true)
    }

    fun stop() = recognizer?.stopListening() ?: Unit

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
        created.startListening(
            Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toLanguageTag())
                .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1),
        )
    }

    private fun release() {
        recognizer?.destroy()
        recognizer = null
    }

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
            firstResult(partialResults)?.let(listener::onPartialTranscript)
        }

        override fun onResults(results: Bundle?) {
            val text = firstResult(results).orEmpty()
            release()
            if (text.isBlank()) listener.onError("I didn’t catch that. Tap the circle to try again.")
            else listener.onFinalTranscript(text)
        }

        override fun onError(error: Int) {
            val missingModel = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
                (error == SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED ||
                    error == SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE) ||
                error == SpeechRecognizer.ERROR_CLIENT && usingOnDevice
            if (usingOnDevice && missingModel && !triedFallback) {
                triedFallback = true
                begin(preferOnDevice = false)
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
