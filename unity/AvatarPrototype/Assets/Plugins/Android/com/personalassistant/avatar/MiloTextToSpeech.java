package com.personalassistant.avatar;

import android.os.Bundle;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.util.Log;

import com.unity3d.player.UnityPlayer;

import java.util.Locale;
import java.util.UUID;

public final class MiloTextToSpeech {
    private static final String TAG = "MiloTTS";
    private static TextToSpeech engine;
    private static boolean initializing;
    private static String pendingTarget;
    private static String pendingText;
    private static String activeTarget;

    private MiloTextToSpeech() { }

    public static void speak(String gameObjectName, String text) {
        if (gameObjectName == null || gameObjectName.isEmpty() || text == null || text.trim().isEmpty()) return;
        UnityPlayer.currentActivity.runOnUiThread(() -> {
            pendingTarget = gameObjectName;
            pendingText = text;
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
                engine.setPitch(1.02f);
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

    private static void speakPending() {
        if (engine == null || pendingText == null) return;
        activeTarget = pendingTarget;
        String utteranceId = "milo-" + UUID.randomUUID();
        Bundle parameters = new Bundle();
        int result = engine.speak(pendingText, TextToSpeech.QUEUE_FLUSH, parameters, utteranceId);
        Log.i(TAG, "Speak requested: id=" + utteranceId + ", chars=" + pendingText.length() + ", result=" + result);
        if (result == TextToSpeech.ERROR) send(activeTarget, "OnTtsError", "speak:error");
        pendingTarget = null;
        pendingText = null;
    }

    public static void stop() {
        UnityPlayer.currentActivity.runOnUiThread(() -> {
            if (engine != null) engine.stop();
        });
    }

    public static void shutdown() {
        UnityPlayer.currentActivity.runOnUiThread(() -> {
            if (engine != null) {
                engine.stop();
                engine.shutdown();
                engine = null;
            }
            initializing = false;
        });
    }

    private static void send(String target, String method, String value) {
        if (target != null) UnityPlayer.UnitySendMessage(target, method, value == null ? "" : value);
    }

    private static final class Listener extends UtteranceProgressListener {
        @Override
        public void onStart(String utteranceId) {
            Log.i(TAG, "Started: " + utteranceId);
            send(activeTarget, "OnTtsStarted", utteranceId);
        }

        @Override
        public void onRangeStart(String utteranceId, int start, int end, int frame) {
            send(activeTarget, "OnTtsRange", "{\"start\":" + start + ",\"end\":" + end + ",\"frame\":" + frame + "}");
        }

        @Override
        public void onDone(String utteranceId) {
            Log.i(TAG, "Done: " + utteranceId);
            send(activeTarget, "OnTtsDone", utteranceId);
        }

        @Override
        @SuppressWarnings("deprecation")
        public void onError(String utteranceId) {
            Log.e(TAG, "Speech failed: " + utteranceId);
            send(activeTarget, "OnTtsError", "utterance:" + utteranceId);
        }

        @Override
        public void onError(String utteranceId, int errorCode) {
            Log.e(TAG, "Speech failed: id=" + utteranceId + ", error=" + errorCode);
            send(activeTarget, "OnTtsError", "utterance:" + utteranceId + ":" + errorCode);
        }
    }
}
