package com.personalassistant.shell

import android.content.Context
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.util.Locale
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

class SpeechOutput(context: Context) : TextToSpeech.OnInitListener {
    private val main = Handler(Looper.getMainLooper())
    private val callbacks = ConcurrentHashMap<String, () -> Unit>()
    private val engine = TextToSpeech(context.applicationContext, this)
    private var ready = false

    override fun onInit(status: Int) {
        ready = status == TextToSpeech.SUCCESS
        if (ready) {
            engine.language = Locale.getDefault()
            engine.setSpeechRate(1.02f)
            engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) = Unit
                override fun onError(utteranceId: String?) = finish(utteranceId)
                override fun onDone(utteranceId: String?) = finish(utteranceId)
            })
        }
    }

    /** Returns false when TTS is not ready; the written response remains available. */
    fun speak(text: String, onDone: () -> Unit): Boolean {
        val spoken = SpeechText.speakable(text)
        if (!ready || spoken.isBlank()) return false
        val id = UUID.randomUUID().toString()
        callbacks[id] = onDone
        val result = engine.speak(spoken.take(2_000), TextToSpeech.QUEUE_FLUSH, Bundle(), id)
        if (result == TextToSpeech.ERROR) {
            callbacks.remove(id)
            return false
        }
        return true
    }

    fun stop() {
        engine.stop()
        callbacks.clear()
    }

    fun close() {
        callbacks.clear()
        engine.stop()
        engine.shutdown()
    }

    private fun finish(id: String?) {
        val callback = id?.let(callbacks::remove) ?: return
        main.post(callback)
    }
}
