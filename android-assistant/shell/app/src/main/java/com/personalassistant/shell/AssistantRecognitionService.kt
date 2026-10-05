package com.personalassistant.shell

import android.content.ComponentName
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognitionService
import android.speech.SpeechRecognizer

/**
 * The speech recognizer Android requires every digital-assistant app to declare.
 *
 * Choosing the app as the assistant can also make this the phone's default recognizer,
 * which other apps' voice input uses, so it never recognizes anything itself: it forwards
 * to Google's recognizer (or the first other one installed).
 */
class AssistantRecognitionService : RecognitionService() {
    private var delegate: SpeechRecognizer? = null

    override fun onStartListening(recognizerIntent: Intent, listener: Callback) {
        delegate?.destroy()
        val target = otherRecognizer()
        if (target == null) {
            runCatching { listener.error(SpeechRecognizer.ERROR_CLIENT) }
            return
        }
        delegate = SpeechRecognizer.createSpeechRecognizer(this, target).apply {
            setRecognitionListener(Forward(listener))
            startListening(recognizerIntent)
        }
    }

    override fun onStopListening(listener: Callback) {
        delegate?.stopListening()
    }

    override fun onCancel(listener: Callback) {
        delegate?.cancel()
    }

    override fun onDestroy() {
        delegate?.destroy()
        delegate = null
        super.onDestroy()
    }

    private fun otherRecognizer(): ComponentName? {
        val services = packageManager
            .queryIntentServices(Intent(SERVICE_INTERFACE), 0)
            .map { ComponentName(it.serviceInfo.packageName, it.serviceInfo.name) }
            .filter { it.packageName != packageName }
        return services.firstOrNull { it.packageName == GOOGLE_SPEECH } ?: services.firstOrNull()
    }

    private class Forward(private val to: Callback) : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) = call { to.readyForSpeech(params ?: Bundle()) }
        override fun onBeginningOfSpeech() = call { to.beginningOfSpeech() }
        override fun onRmsChanged(rmsdB: Float) = call { to.rmsChanged(rmsdB) }
        override fun onBufferReceived(buffer: ByteArray?) = call { buffer?.let(to::bufferReceived) }
        override fun onEndOfSpeech() = call { to.endOfSpeech() }
        override fun onError(error: Int) = call { to.error(error) }
        override fun onResults(results: Bundle?) = call { to.results(results ?: Bundle()) }
        override fun onPartialResults(partialResults: Bundle?) =
            call { to.partialResults(partialResults ?: Bundle()) }
        override fun onEvent(eventType: Int, params: Bundle?) = Unit

        // The caller may have gone away; that is not this service's failure.
        private inline fun call(block: () -> Unit) {
            runCatching(block)
        }
    }

    private companion object {
        const val GOOGLE_SPEECH = "com.google.android.tts"
    }
}
