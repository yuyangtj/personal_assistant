package com.personalassistant.avatar;

import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTimestamp;
import android.media.AudioTrack;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.speech.tts.Voice;
import android.util.Log;

import com.unity3d.player.UnityPlayer;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Android TTS bridge for Milo. Speech is first synthesized to a WAV file so every word's
 * audio frame and the loudness envelope are known before playback; Unity then follows
 * the AudioTrack presentation clock. Engines that cannot synthesize to a file fall back
 * to live speech with range callbacks.
 */
public final class MiloTextToSpeech {
    private static final String TAG = "MiloTTS";
    private static final int ENVELOPE_RATE = 60;
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static final ExecutorService WORKER = Executors.newSingleThreadExecutor();

    private static TextToSpeech engine;
    private static boolean initializing;
    private static String pendingTarget;
    private static String pendingText;
    private static String pendingVoiceStyle = "default";
    private static String appliedVoiceStyle;
    private static Voice defaultVoice;

    // Google TTS US-English male voices, most natural first. Other engines fall back to name hints or pitch.
    private static final String[] MALE_VOICES = {
            "en-us-x-iom-local", "en-us-x-tpd-local", "en-us-x-iol-local",
            "en-us-x-iom-network", "en-us-x-tpd-network", "en-us-x-iol-network"
    };
    private static volatile Utterance active;
    private static volatile AudioTrack track;
    private static volatile int trackSampleRate;

    private MiloTextToSpeech() { }

    private static final class Utterance {
        final String id;
        final String target;
        final String text;
        final File file;
        final List<int[]> ranges = new ArrayList<>();
        boolean live;
        boolean cancelled;

        Utterance(String id, String target, String text, File file) {
            this.id = id;
            this.target = target;
            this.text = text;
            this.file = file;
        }
    }

    public static void speak(String gameObjectName, String text) {
        speak(gameObjectName, text, "default");
    }

    /** @param voiceStyle "male" selects a male English voice; "default" keeps the engine's voice. */
    public static void speak(String gameObjectName, String text, String voiceStyle) {
        if (gameObjectName == null || gameObjectName.isEmpty() || text == null || text.trim().isEmpty()) return;
        UnityPlayer.currentActivity.runOnUiThread(() -> {
            pendingTarget = gameObjectName;
            pendingText = text;
            pendingVoiceStyle = voiceStyle == null ? "default" : voiceStyle;
            if (engine != null && !initializing) {
                speakPending();
                return;
            }
            if (initializing) return;

            initializing = true;
            engine = new TextToSpeech(UnityPlayer.currentActivity, status -> {
                initializing = false;
                if (status != TextToSpeech.SUCCESS) {
                    Log.e(TAG, "Initialization failed: " + status);
                    send(pendingTarget, "OnTtsError", "initialization:" + status);
                    return;
                }

                int languageResult = engine.setLanguage(Locale.US);
                engine.setSpeechRate(0.95f);
                defaultVoice = engine.getVoice();
                engine.setOnUtteranceProgressListener(new Listener());
                if (languageResult == TextToSpeech.LANG_MISSING_DATA || languageResult == TextToSpeech.LANG_NOT_SUPPORTED) {
                    Log.e(TAG, "English language data is unavailable: " + languageResult);
                    send(pendingTarget, "OnTtsError", "language:" + languageResult);
                    return;
                }
                speakPending();
            });
        });
    }

    /** Presentation position of the playing utterance in milliseconds, or -1 when idle. */
    public static long getPlaybackPositionMs() {
        AudioTrack current = track;
        int sampleRate = trackSampleRate;
        if (current == null || sampleRate <= 0) return -1;
        try {
            if (current.getPlayState() != AudioTrack.PLAYSTATE_PLAYING) return -1;
            AudioTimestamp timestamp = new AudioTimestamp();
            if (current.getTimestamp(timestamp)) {
                long elapsedFrames = (System.nanoTime() - timestamp.nanoTime) * sampleRate / 1_000_000_000L;
                return Math.max(0, (timestamp.framePosition + elapsedFrames) * 1000L / sampleRate);
            }
            return (current.getPlaybackHeadPosition() & 0xFFFFFFFFL) * 1000L / sampleRate;
        } catch (IllegalStateException exception) {
            return -1;
        }
    }

    private static void speakPending() {
        if (engine == null || pendingText == null) return;
        cancelActive();
        applyVoiceStyle(pendingVoiceStyle);

        File directory = new File(UnityPlayer.currentActivity.getCacheDir(), "milo-tts");
        if (!directory.isDirectory() && !directory.mkdirs()) Log.w(TAG, "Cache directory unavailable: " + directory);
        File[] stale = directory.listFiles();
        if (stale != null) for (File file : stale) if (!file.delete()) Log.w(TAG, "Could not delete " + file);

        String utteranceId = "milo-" + UUID.randomUUID();
        Utterance utterance = new Utterance(utteranceId, pendingTarget, pendingText, new File(directory, utteranceId + ".wav"));
        active = utterance;
        pendingTarget = null;
        pendingText = null;

        int result = engine.synthesizeToFile(utterance.text, new Bundle(), utterance.file, utteranceId);
        Log.i(TAG, "Synthesis requested: id=" + utteranceId + ", chars=" + utterance.text.length() + ", result=" + result);
        if (result == TextToSpeech.ERROR) speakLive(utterance, "synthesize:error");
    }

    private static void applyVoiceStyle(String style) {
        if (style.equals(appliedVoiceStyle)) return;
        appliedVoiceStyle = style;
        if (!"male".equals(style)) {
            if (defaultVoice != null) engine.setVoice(defaultVoice);
            engine.setPitch(1.02f);
            Log.i(TAG, "TTS_VOICE style=" + style + " voice=" + (defaultVoice == null ? "engine-default" : defaultVoice.getName()));
            return;
        }

        Voice chosen = null;
        java.util.Set<Voice> voices = engine.getVoices();
        if (voices != null) {
            StringBuilder english = new StringBuilder();
            for (Voice voice : voices) {
                if (isUsableEnglish(voice)) english.append(voice.getName()).append(' ');
            }
            Log.i(TAG, "Installed US-English voices: " + english.toString().trim());
            for (String name : MALE_VOICES) {
                for (Voice voice : voices) {
                    if (voice.getName().equalsIgnoreCase(name) && isUsableEnglish(voice)) {
                        chosen = voice;
                        break;
                    }
                }
                if (chosen != null) break;
            }
            if (chosen == null) {
                for (Voice voice : voices) {
                    String name = voice.getName().toLowerCase(Locale.US);
                    if (isUsableEnglish(voice) && name.contains("male") && !name.contains("female")) {
                        chosen = voice;
                        break;
                    }
                }
            }
        }

        if (chosen != null && engine.setVoice(chosen) == TextToSpeech.SUCCESS) {
            engine.setPitch(0.96f);
            Log.i(TAG, "TTS_VOICE style=male voice=" + chosen.getName());
        } else {
            // No male voice installed: deepen the default voice instead.
            if (defaultVoice != null) engine.setVoice(defaultVoice);
            engine.setPitch(0.78f);
            Log.w(TAG, "TTS_VOICE style=male voice=pitch-fallback (no male voice installed)");
        }
    }

    private static boolean isUsableEnglish(Voice voice) {
        Locale locale = voice.getLocale();
        boolean english = locale != null && "en".equals(locale.getLanguage()) && "US".equals(locale.getCountry());
        boolean installed = voice.getFeatures() == null || !voice.getFeatures().contains(TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED);
        return english && installed && !voice.isNetworkConnectionRequired();
    }

    private static void speakLive(Utterance utterance, String reason) {
        if (utterance.cancelled || engine == null) return;
        Log.w(TAG, "TTS_LIVE_FALLBACK: " + reason);
        utterance.live = true;
        utterance.ranges.clear();
        int result = engine.speak(utterance.text, TextToSpeech.QUEUE_FLUSH, new Bundle(), utterance.id);
        if (result == TextToSpeech.ERROR) send(utterance.target, "OnTtsError", "speak:error");
    }

    public static void stop() {
        UnityPlayer.currentActivity.runOnUiThread(MiloTextToSpeech::cancelActive);
    }

    public static void shutdown() {
        UnityPlayer.currentActivity.runOnUiThread(() -> {
            cancelActive();
            if (engine != null) {
                engine.shutdown();
                engine = null;
            }
            initializing = false;
        });
    }

    private static void cancelActive() {
        if (active != null) active.cancelled = true;
        active = null;
        if (engine != null) engine.stop();
        releaseTrack();
    }

    private static void releaseTrack() {
        AudioTrack current = track;
        track = null;
        trackSampleRate = 0;
        if (current == null) return;
        try {
            current.stop();
        } catch (IllegalStateException ignored) {
            // Already stopped.
        }
        current.release();
    }

    private static void onSynthesisDone(Utterance utterance) {
        WORKER.execute(() -> {
            try {
                Wave wave = Wave.read(utterance.file);
                String timeline = buildTimeline(utterance, wave);
                MAIN.post(() -> play(utterance, wave, timeline));
            } catch (IOException | RuntimeException exception) {
                Log.e(TAG, "Synthesized audio unreadable", exception);
                MAIN.post(() -> speakLive(utterance, "wave:" + exception.getMessage()));
            }
        });
    }

    private static void play(Utterance utterance, Wave wave, String timeline) {
        if (utterance.cancelled || utterance != active) return;
        releaseTrack();

        int channelMask = wave.channels == 2 ? AudioFormat.CHANNEL_OUT_STEREO : AudioFormat.CHANNEL_OUT_MONO;
        AudioTrack audio = new AudioTrack.Builder()
                .setAudioAttributes(new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ASSISTANT)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build())
                .setAudioFormat(new AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(wave.sampleRate)
                        .setChannelMask(channelMask)
                        .build())
                .setTransferMode(AudioTrack.MODE_STATIC)
                .setBufferSizeInBytes(Math.max(wave.pcm.length, 4))
                .build();
        audio.write(wave.pcm, 0, wave.pcm.length);

        int totalFrames = wave.frameCount();
        audio.setNotificationMarkerPosition(Math.max(1, totalFrames - 1));
        audio.setPlaybackPositionUpdateListener(new AudioTrack.OnPlaybackPositionUpdateListener() {
            @Override
            public void onMarkerReached(AudioTrack reached) {
                finishPlayback(utterance, audio);
            }

            @Override
            public void onPeriodicNotification(AudioTrack ignored) { }
        }, MAIN);

        track = audio;
        trackSampleRate = wave.sampleRate;
        Log.i(TAG, "TTS_PITCH voice=" + (engine.getVoice() == null ? "?" : engine.getVoice().getName()) + " medianF0Hz=" + wave.medianPitchHz());
        send(utterance.target, "OnTtsTimeline", timeline);
        audio.play();
        send(utterance.target, "OnTtsStarted", utterance.id);
        Log.i(TAG, "Playback started: id=" + utterance.id + ", ms=" + wave.durationMs() + ", rate=" + wave.sampleRate + ", ranges=" + utterance.ranges.size());

        // Marker callbacks can be skipped by some audio HALs; the timeout guarantees completion.
        MAIN.postDelayed(() -> finishPlayback(utterance, audio), wave.durationMs() + 400L);
    }

    private static void finishPlayback(Utterance utterance, AudioTrack audio) {
        if (track != audio || utterance.cancelled) return;
        releaseTrack();
        if (active == utterance) active = null;
        if (!utterance.file.delete()) Log.w(TAG, "Could not delete " + utterance.file);
        Log.i(TAG, "Done: " + utterance.id);
        send(utterance.target, "OnTtsDone", utterance.id);
    }

    private static String buildTimeline(Utterance utterance, Wave wave) {
        StringBuilder json = new StringBuilder(256 + utterance.ranges.size() * 32);
        json.append("{\"utteranceId\":\"").append(utterance.id)
                .append("\",\"sampleRate\":").append(wave.sampleRate)
                .append(",\"durationMs\":").append(wave.durationMs())
                .append(",\"envelopeRate\":").append(ENVELOPE_RATE)
                .append(",\"words\":[");
        synchronized (utterance.ranges) {
            for (int i = 0; i < utterance.ranges.size(); i++) {
                int[] range = utterance.ranges.get(i);
                if (i > 0) json.append(',');
                json.append("{\"start\":").append(range[0]).append(",\"end\":").append(range[1]).append(",\"frame\":").append(range[2]).append('}');
            }
        }
        json.append("],\"envelope\":[");
        int[] envelope = wave.envelope(ENVELOPE_RATE);
        for (int i = 0; i < envelope.length; i++) {
            if (i > 0) json.append(',');
            json.append(envelope[i]);
        }
        return json.append("]}").toString();
    }

    private static void send(String target, String method, String value) {
        if (target != null) UnityPlayer.UnitySendMessage(target, method, value == null ? "" : value);
    }

    private static final class Wave {
        int sampleRate;
        int channels;
        byte[] pcm;

        static Wave read(File file) throws IOException {
            byte[] bytes = new byte[(int) file.length()];
            try (FileInputStream input = new FileInputStream(file)) {
                int offset = 0;
                while (offset < bytes.length) {
                    int read = input.read(bytes, offset, bytes.length - offset);
                    if (read < 0) break;
                    offset += read;
                }
            }

            ByteBuffer buffer = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
            if (bytes.length < 12 || buffer.getInt(0) != 0x46464952 || buffer.getInt(8) != 0x45564157)
                throw new IOException("not a RIFF/WAVE file");

            Wave wave = new Wave();
            int bits = 0;
            int position = 12;
            while (position + 8 <= bytes.length) {
                int chunkId = buffer.getInt(position);
                int chunkSize = buffer.getInt(position + 4);
                int body = position + 8;
                if (chunkId == 0x20746d66) { // "fmt "
                    wave.channels = buffer.getShort(body + 2);
                    wave.sampleRate = buffer.getInt(body + 4);
                    bits = buffer.getShort(body + 14);
                } else if (chunkId == 0x61746164) { // "data"
                    int size = chunkSize < 0 || body + chunkSize > bytes.length ? bytes.length - body : chunkSize;
                    wave.pcm = new byte[size - size % 2];
                    System.arraycopy(bytes, body, wave.pcm, 0, wave.pcm.length);
                    break;
                }
                position = body + chunkSize + (chunkSize & 1);
            }
            if (wave.pcm == null || bits != 16 || wave.sampleRate <= 0 || wave.channels < 1)
                throw new IOException("unsupported WAV format bits=" + bits + " rate=" + wave.sampleRate);
            return wave;
        }

        int frameCount() {
            return pcm.length / (2 * channels);
        }

        /** Median fundamental frequency of voiced 40 ms windows, by autocorrelation (diagnostic). */
        int medianPitchHz() {
            int window = sampleRate / 25;
            int frames = frameCount();
            ByteBuffer samples = ByteBuffer.wrap(pcm).order(ByteOrder.LITTLE_ENDIAN);
            List<Integer> estimates = new ArrayList<>();
            double[] x = new double[window];
            int minLag = sampleRate / 400;
            int maxLag = sampleRate / 60;
            for (int start = 0; start + window < frames; start += window) {
                double energy = 0;
                for (int i = 0; i < window; i++) {
                    x[i] = samples.getShort((start + i) * 2 * channels) / 32768.0;
                    energy += x[i] * x[i];
                }
                if (Math.sqrt(energy / window) < 0.03) continue;
                double best = 0;
                int bestLag = 0;
                for (int lag = minLag; lag <= maxLag && lag < window; lag++) {
                    double sum = 0;
                    for (int i = 0; i + lag < window; i++) sum += x[i] * x[i + lag];
                    if (sum > best) {
                        best = sum;
                        bestLag = lag;
                    }
                }
                if (bestLag > 0 && best > energy * 0.3) estimates.add(sampleRate / bestLag);
            }
            if (estimates.isEmpty()) return 0;
            java.util.Collections.sort(estimates);
            return estimates.get(estimates.size() / 2);
        }

        long durationMs() {
            return frameCount() * 1000L / sampleRate;
        }

        /** RMS loudness per window, normalized to 0-100 against the utterance's loud speech. */
        int[] envelope(int rate) {
            int window = Math.max(1, sampleRate / rate);
            int frames = frameCount();
            double[] rms = new double[(frames + window - 1) / window];
            ByteBuffer samples = ByteBuffer.wrap(pcm).order(ByteOrder.LITTLE_ENDIAN);
            for (int w = 0; w < rms.length; w++) {
                double sum = 0;
                int start = w * window;
                int end = Math.min(frames, start + window);
                for (int frame = start; frame < end; frame++) {
                    double value = samples.getShort(frame * 2 * channels) / 32768.0;
                    sum += value * value;
                }
                rms[w] = Math.sqrt(sum / Math.max(1, end - start));
            }
            double[] sorted = rms.clone();
            Arrays.sort(sorted);
            double reference = Math.max(1e-4, sorted.length == 0 ? 0 : sorted[(int) (sorted.length * 0.95)]);
            int[] result = new int[rms.length];
            for (int w = 0; w < rms.length; w++)
                result[w] = (int) Math.round(Math.min(1.0, Math.sqrt(rms[w] / reference)) * 100);
            return result;
        }
    }

    private static final class Listener extends UtteranceProgressListener {
        private static Utterance find(String utteranceId) {
            Utterance current = active;
            return current != null && current.id.equals(utteranceId) ? current : null;
        }

        @Override
        public void onStart(String utteranceId) {
            Utterance utterance = find(utteranceId);
            if (utterance == null || !utterance.live) return;
            Log.i(TAG, "Live speech started: " + utteranceId);
            send(utterance.target, "OnTtsStarted", utteranceId);
        }

        @Override
        public void onRangeStart(String utteranceId, int start, int end, int frame) {
            Utterance utterance = find(utteranceId);
            if (utterance == null) return;
            // Some engines (Google TTS on Android 17) deliver (frame, start, end) instead of the
            // documented (start, end, frame). A valid character range always has start < end.
            int length = utterance.text.length();
            if (end < start && frame > end && frame <= length) {
                int audioFrame = start;
                start = end;
                end = frame;
                frame = audioFrame;
            }
            if (utterance.live) {
                send(utterance.target, "OnTtsRange", "{\"start\":" + start + ",\"end\":" + end + ",\"frame\":" + frame + "}");
                return;
            }
            synchronized (utterance.ranges) {
                utterance.ranges.add(new int[] { start, end, frame });
            }
        }

        @Override
        public void onDone(String utteranceId) {
            Utterance utterance = find(utteranceId);
            if (utterance == null || utterance.cancelled) return;
            if (!utterance.live) {
                onSynthesisDone(utterance);
                return;
            }
            Log.i(TAG, "Live speech done: " + utteranceId);
            MAIN.post(() -> {
                if (active == utterance) active = null;
            });
            send(utterance.target, "OnTtsDone", utteranceId);
        }

        @Override
        @SuppressWarnings("deprecation")
        public void onError(String utteranceId) {
            onError(utteranceId, TextToSpeech.ERROR);
        }

        @Override
        public void onError(String utteranceId, int errorCode) {
            Utterance utterance = find(utteranceId);
            if (utterance == null || utterance.cancelled) return;
            Log.e(TAG, "Speech failed: id=" + utteranceId + ", live=" + utterance.live + ", error=" + errorCode);
            if (!utterance.live) {
                MAIN.post(() -> speakLive(utterance, "synthesis:" + errorCode));
                return;
            }
            send(utterance.target, "OnTtsError", "utterance:" + utteranceId + ":" + errorCode);
        }
    }
}
