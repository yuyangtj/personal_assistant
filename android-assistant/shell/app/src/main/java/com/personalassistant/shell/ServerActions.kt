package com.personalassistant.shell

import android.util.Base64
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import org.json.JSONObject

/**
 * Phone actions the assistant proposed in a reply (a timer, an alarm, a calendar draft).
 *
 * The page only says which reply card it is. The app reads the action from the server
 * itself, with the console login, checks it as strictly as its own on-device actions, and
 * runs it only after the user confirms the label it shows.
 */
object ServerActions {
    private val ID = Regex("^[A-Za-z0-9-]{1,64}$")
    private const val TIMEOUT_MS = 8_000

    /** The server path of a card, from the page's "chat/message" and the block id; null if malformed. */
    fun path(reference: String, blockId: String): String? {
        val parts = reference.split("/")
        if (parts.size != 2 || !parts.all(ID::matches) || !ID.matches(blockId)) return null
        return "/chat-sessions/${parts[0]}/messages/${parts[1]}/phone-actions/$blockId"
    }

    /** GET the card's action from the console's own server; null on any failure. */
    fun fetch(origin: String, path: String, login: Pair<String, String>?): JSONObject? = runCatching {
        val connection = URL(origin + path).openConnection() as HttpURLConnection
        connection.connectTimeout = TIMEOUT_MS
        connection.readTimeout = TIMEOUT_MS
        connection.instanceFollowRedirects = false
        if (login != null) {
            val token = Base64.encodeToString("${login.first}:${login.second}".toByteArray(), Base64.NO_WRAP)
            connection.setRequestProperty("Authorization", "Basic $token")
        }
        try {
            if (connection.responseCode != 200) return@runCatching null
            JSONObject(connection.inputStream.bufferedReader().use { it.readText().take(20_000) })
        } finally {
            connection.disconnect()
        }
    }.getOrNull()

    fun host(origin: String): String? = runCatching { URI(origin).host }.getOrNull()

    /** The server's action as one this app runs, or null when anything about it is off. */
    fun toLocal(action: JSONObject, zone: ZoneId, now: Instant = Instant.now()): LocalAction? =
        when (action.optString("type")) {
            "set_timer" -> {
                val seconds = action.optInt("seconds", -1)
                if (seconds !in 1..86_400) null else LocalAction(
                    kind = LocalActionKind.TIMER,
                    label = "Set a timer for ${duration(seconds)}",
                    seconds = seconds,
                )
            }
            "set_alarm" -> alarm(action, zone, now)
            "create_event" -> event(action, zone, now)
            else -> null
        }

    private fun alarm(action: JSONObject, zone: ZoneId, now: Instant): LocalAction? {
        val hour = action.optInt("hour", -1)
        val minute = action.optInt("minute", -1)
        if (hour !in 0..23 || minute !in 0..59) return null
        val days = action.optJSONArray("days")?.let { array -> (0 until array.length()).map { array.optInt(it, -1) } }
            ?: emptyList()
        if (days.any { it !in 1..7 } || days.toSet().size != days.size) return null
        val date = action.optString("date").takeIf { it.isNotBlank() && it != "null" }
            ?.let { runCatching { LocalDate.parse(it) }.getOrNull() ?: return null }
        if (days.isEmpty() && date == null) return null
        if (date != null && date.isBefore(LocalDate.ofInstant(now, zone))) return null
        val time = "%02d:%02d".format(hour, minute)
        return LocalAction(
            kind = LocalActionKind.ALARM,
            label = if (days.isEmpty()) "Set an alarm for $time" else "Set a repeating alarm for $time",
            hour = hour,
            minute = minute,
            date = date,
            days = days,
        )
    }

    private fun event(action: JSONObject, zone: ZoneId, now: Instant): LocalAction? {
        val title = action.optString("title").trim().take(120)
        if (title.isBlank()) return null
        val start = local(action.optString("start"), zone) ?: return null
        val end = action.optString("end").takeIf { it.isNotBlank() && it != "null" }
            ?.let { local(it, zone) ?: return null }
            ?: start.plus(Duration.ofHours(1))
        val length = Duration.between(start, end)
        if (
            start.isBefore(now.minus(Duration.ofMinutes(5))) ||
            start.isAfter(now.plus(Duration.ofDays(366))) ||
            length < Duration.ofMinutes(5) ||
            length > Duration.ofHours(24)
        ) return null
        return LocalAction(
            kind = LocalActionKind.CALENDAR,
            label = "Draft “$title” in Calendar",
            title = title,
            start = start,
            end = end,
            location = action.optString("location").trim().take(120),
        )
    }

    /** "YYYY-MM-DDTHH:MM" in the phone's own time zone. */
    private fun local(value: String, zone: ZoneId): Instant? =
        runCatching { LocalDateTime.parse(value.trim()).atZone(zone).toInstant() }.getOrNull()

    private fun duration(seconds: Int): String = when {
        seconds % 3600 == 0 -> "${seconds / 3600} hour" + if (seconds == 3600) "" else "s"
        seconds % 60 == 0 -> "${seconds / 60} minute" + if (seconds == 60) "" else "s"
        else -> "$seconds second" + if (seconds == 1) "" else "s"
    }
}
