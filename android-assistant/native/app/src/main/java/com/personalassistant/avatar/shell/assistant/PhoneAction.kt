package com.personalassistant.avatar.shell.assistant

import java.time.LocalDate
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException
import org.json.JSONObject

/** A backend-proposed action that runs on the phone only after the user confirms it. */
sealed interface PhoneAction {
    /** Short line for the confirmation card, e.g. "Timer · 10 min · Tea". */
    val summary: String

    /** What the avatar says after the action succeeds. */
    val doneSpeech: String

    data class SetTimer(val seconds: Int, val label: String) : PhoneAction {
        override val summary get() = listOf("Timer", duration(seconds), label).filter { it.isNotBlank() }.joinToString(" · ")
        override val doneSpeech get() = "Done. Your ${spokenDuration(seconds)} timer is running."
    }

    data class SetAlarm(
        val hour: Int,
        val minute: Int,
        val label: String,
        val days: List<Int>,
        val date: LocalDate?,
    ) : PhoneAction {
        override val summary get() = listOf("Alarm", date?.format(DateTimeFormatter.ofPattern("EEE d MMM")).orEmpty(), "%02d:%02d".format(hour, minute), repeat(days), label)
            .filter { it.isNotBlank() }.joinToString(" · ")
        override val doneSpeech get() = "Done. Your alarm is set for %d:%02d.".format(hour, minute)
    }

    data class CreateEvent(val title: String, val start: LocalDateTime, val end: LocalDateTime?, val location: String) : PhoneAction {
        override val summary get() = listOf(
            "Event",
            title,
            start.format(DateTimeFormatter.ofPattern("EEE d MMM, HH:mm")) + (end?.let { "–" + it.format(DateTimeFormatter.ofPattern("HH:mm")) } ?: ""),
            location,
        ).filter { it.isNotBlank() }.joinToString(" · ")
        override val doneSpeech get() = "I opened your calendar with the event filled in. Tap save there to keep it."
    }

    companion object {
        /** Parses the `action` of an ASSISTANT_REPLY payload; unknown or malformed actions are ignored. */
        fun fromJson(json: JSONObject?): PhoneAction? {
            if (json == null) return null
            return try {
                when (json.optString("type")) {
                    "set_timer" -> SetTimer(json.getInt("seconds").coerceIn(1, 86_400), json.optString("label"))
                    "set_alarm" -> {
                        val days = json.optJSONArray("days")?.let { values ->
                            List(values.length()) { values.getInt(it) }
                        } ?: emptyList()
                        val date = json.optString("date").takeIf {
                            it.isNotBlank() && it != "null"
                        }?.let(LocalDate::parse)
                        if ((days.isEmpty() && date == null) || (days.isNotEmpty() && date != null)) {
                            null
                        } else {
                            SetAlarm(
                                json.getInt("hour").coerceIn(0, 23),
                                json.getInt("minute").coerceIn(0, 59),
                                json.optString("label"),
                                days,
                                date,
                            )
                        }
                    }
                    "create_event" -> CreateEvent(
                        json.getString("title"),
                        LocalDateTime.parse(json.getString("start")),
                        json.optString("end").takeIf { it.isNotBlank() && it != "null" }?.let(LocalDateTime::parse),
                        json.optString("location"),
                    )
                    else -> null
                }
            } catch (_: org.json.JSONException) {
                null
            } catch (_: DateTimeParseException) {
                null
            }
        }

        private fun duration(seconds: Int): String = when {
            seconds % 3600 == 0 -> "${seconds / 3600} h"
            seconds >= 3600 -> "${seconds / 3600} h ${seconds % 3600 / 60} min"
            seconds % 60 == 0 -> "${seconds / 60} min"
            seconds > 60 -> "${seconds / 60} min ${seconds % 60} s"
            else -> "$seconds s"
        }

        private fun spokenDuration(seconds: Int): String = when {
            seconds % 3600 == 0 -> "${seconds / 3600} hour"
            seconds % 60 == 0 -> "${seconds / 60} minute"
            else -> "$seconds second"
        }

        private fun repeat(days: List<Int>): String {
            if (days.isEmpty()) return ""
            val names = listOf("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
            return days.sorted().joinToString(",") { names[(it - 1).coerceIn(0, 6)] }
        }
    }
}
