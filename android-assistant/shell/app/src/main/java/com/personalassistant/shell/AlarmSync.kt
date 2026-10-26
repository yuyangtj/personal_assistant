package com.personalassistant.shell

import android.content.Context
import android.util.Base64
import androidx.core.app.NotificationManagerCompat
import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject

/**
 * Brings the phone's reminders and alarms in line with the server: GET /phone/alarms, set
 * them with Android, then tell the server up to when the phone has each one, so it sends
 * no second notification through ntfy. Runs off the main thread: when the app opens, after
 * a reply, and after one rings (to pick up the next time of a repeating one).
 */
object AlarmSync {
    private const val TIMEOUT_MS = 10_000

    /** False when the server couldn't be reached or the phone can't show notifications. */
    @Synchronized
    fun sync(context: Context): Boolean {
        // Without notifications nothing would ring: leave them to the server's push.
        if (!NotificationManagerCompat.from(context).areNotificationsEnabled()) return false
        val server = context.getSharedPreferences("shell", Context.MODE_PRIVATE)
            .getString(MainActivity.KEY_SERVER, null) ?: BuildConfig.CONSOLE_URL
        val origin = ConsoleUrls.origin(server) ?: return false
        val login = ServerActions.host(origin)?.let(CredentialStore(context)::load)
        val body = request(origin + "/phone/alarms", login, null) ?: return false
        val now = System.currentTimeMillis()
        val fetched = runCatching { PhoneAlarms.parse(JSONObject(body), now) }.getOrNull() ?: return false
        val scheduler = AlarmScheduler(context)
        val plan = PhoneAlarms.plan(scheduler.stored(), fetched, now)
        scheduler.apply(plan)
        request(origin + "/phone/alarms/ack", login, PhoneAlarms.ack(plan.all).toString())
        return true
    }

    private fun request(url: String, login: Pair<String, String>?, post: String?): String? = runCatching {
        val connection = URL(url).openConnection() as HttpURLConnection
        connection.connectTimeout = TIMEOUT_MS
        connection.readTimeout = TIMEOUT_MS
        connection.instanceFollowRedirects = false
        if (login != null) {
            val token = Base64.encodeToString("${login.first}:${login.second}".toByteArray(), Base64.NO_WRAP)
            connection.setRequestProperty("Authorization", "Basic $token")
        }
        if (post != null) {
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(post.toByteArray()) }
        }
        try {
            if (connection.responseCode != 200) return@runCatching null
            connection.inputStream.bufferedReader().use { it.readText().take(200_000) }
        } finally {
            connection.disconnect()
        }
    }.getOrNull()
}
