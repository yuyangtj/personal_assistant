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
    /** What each queued utterance says, to tell the page which word is being spoken. */
    private val texts = ConcurrentHashMap<String, String>()

    /**
     * Speech as it happens, for the avatar's lip sync, on the main thread: "start" when the
     * phone starts speaking, "word" (with the word) as each word is spoken, if the speech
     * engine reports words, and "done" when nothing is left to say.
     */
    var events: ((state: String, word: String?) -> Unit)? = null
    private val engine = TextToSpeech(context.applicationContext, this)
    private var ready = false

    override fun onInit(status: Int) {
        ready = status == TextToSpeech.SUCCESS
        if (ready) {
            engine.language = Locale.getDefault()
            engine.setSpeechRate(1.02f)
            engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) = emit("start", null)
                override fun onRangeStart(utteranceId: String?, start: Int, end: Int, frame: Int) {
                    val text = utteranceId?.let(texts::get) ?: return
                    if (start in 0 until end && end <= text.length) emit("word", text.substring(start, end))
                }
                override fun onError(utteranceId: String?) = finish(utteranceId)
                override fun onDone(utteranceId: String?) = finish(utteranceId)
            })
        }
    }

    /** Returns false when TTS is not ready; the written response remains available. */
    /** Speak now, or ([queue]) after what is being said: a reply spoken a sentence at a time. */
    fun speak(text: String, queue: Boolean = false, onDone: () -> Unit): Boolean {
        val spoken = SpeechText.speakable(text)
        if (!ready || spoken.isBlank()) return false
        val id = UUID.randomUUID().toString()
        callbacks[id] = onDone
        texts[id] = spoken.take(2_000)
        val mode = if (queue) TextToSpeech.QUEUE_ADD else TextToSpeech.QUEUE_FLUSH
        val result = engine.speak(spoken.take(2_000), mode, Bundle(), id)
        if (result == TextToSpeech.ERROR) {
            callbacks.remove(id)
            texts.remove(id)
            return false
        }
        return true
    }

    fun stop() {
        engine.stop()
        callbacks.clear()
        if (texts.isNotEmpty()) {
            texts.clear()
            emit("done", null)
        }
    }

    fun close() {
        callbacks.clear()
        engine.stop()
        engine.shutdown()
    }

    private fun finish(id: String?) {
        id?.let(texts::remove)
        if (texts.isEmpty()) emit("done", null)
        val callback = id?.let(callbacks::remove) ?: return
        main.post(callback)
    }

    private fun emit(state: String, word: String?) {
        val listener = events ?: return
        main.post { listener(state, word) }
    }
}
