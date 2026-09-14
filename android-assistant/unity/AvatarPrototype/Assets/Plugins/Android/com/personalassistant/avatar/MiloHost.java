package com.personalassistant.avatar;

/**
 * Link between Unity and a native Android host app that embeds Unity as a library.
 * The host marks itself embedded before Unity starts, and receives speech notifications.
 */
public final class MiloHost {
    /** Receives avatar speech lifecycle callbacks on the Unity main thread. */
    public interface Listener {
        void onSpeechStarted(String responseId);

        void onSpeechFinished(String responseId, String reason);
    }

    private static volatile boolean embedded;
    private static volatile Listener listener;

    private MiloHost() { }

    /** Call before the Unity activity is created to hide Unity's own prototype controls. */
    public static void setEmbedded(boolean value) {
        embedded = value;
    }

    public static boolean isEmbedded() {
        return embedded;
    }

    public static void setListener(Listener value) {
        listener = value;
    }

    public static void notifySpeechStarted(String responseId) {
        Listener current = listener;
        if (current != null) current.onSpeechStarted(responseId == null ? "" : responseId);
    }

    public static void notifySpeechFinished(String responseId, String reason) {
        Listener current = listener;
        if (current != null) current.onSpeechFinished(responseId == null ? "" : responseId, reason == null ? "" : reason);
    }
}
