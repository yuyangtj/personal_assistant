package com.personalassistant.shell

import org.json.JSONArray
import org.json.JSONObject

/**
 * One time a reminder or alarm rings on this phone. [alarm]: rings like a clock alarm
 * (full screen, until dismissed); otherwise a notification with sound. A snooze is the
 * phone's own and never comes from the server.
 */
data class PhoneAlarm(
    val id: String,
    val scheduleId: String,
    val at: Long,
    val alarm: Boolean,
    val text: String,
    val snooze: Boolean = false,
)

/** What to change so the phone holds exactly what the server lists. */
data class AlarmPlan(val add: List<PhoneAlarm>, val cancel: List<PhoneAlarm>, val keep: List<PhoneAlarm>) {
    val all: List<PhoneAlarm> get() = keep + add
}

/**
 * The reminders and alarms the server lists for the phone (GET /phone/alarms), checked as
 * strictly as a phone action: anything off is left out, never guessed.
 */
object PhoneAlarms {
    private val ID = Regex("^[A-Za-z0-9-]{1,64}$")
    const val MAX = 64
    const val MAX_TEXT = 300
    /** The server sends two weeks; a little more is allowed for clock differences. */
    const val HORIZON_MS = 15L * 24 * 60 * 60 * 1000
    const val SNOOZE_MS = 10L * 60 * 1000

    fun parse(body: JSONObject, now: Long): List<PhoneAlarm> {
        val list = body.optJSONArray("alarms") ?: return emptyList()
        val alarms = mutableListOf<PhoneAlarm>()
        for (index in 0 until minOf(list.length(), MAX)) {
            val item = list.optJSONObject(index) ?: continue
            val scheduleId = item.optString("schedule_id")
            val at = item.optLong("at", -1)
            val kind = item.optString("kind")
            val text = item.optString("text").trim()
            if (!ID.matches(scheduleId) || kind !in setOf("alarm", "reminder")) continue
            if (at <= now || at > now + HORIZON_MS || text.isEmpty()) continue
            alarms += PhoneAlarm(
                id = "$scheduleId@${at / 1000}",
                scheduleId = scheduleId,
                at = at,
                alarm = kind == "alarm",
                text = text.take(MAX_TEXT),
            )
        }
        return alarms.distinctBy { it.id }
    }

    /** Server rings not held yet are added, held ones it no longer lists are cancelled. */
    fun plan(stored: List<PhoneAlarm>, fetched: List<PhoneAlarm>, now: Long): AlarmPlan {
        val wanted = fetched.associateBy { it.id }
        val keep = mutableListOf<PhoneAlarm>()
        val cancel = mutableListOf<PhoneAlarm>()
        for (alarm in stored) {
            when {
                alarm.at <= now -> cancel += alarm
                alarm.snooze -> keep += alarm  // the phone's own, until it rings
                wanted[alarm.id] == alarm -> keep += alarm
                else -> cancel += alarm
            }
        }
        val held = keep.map { it.id }.toSet()
        return AlarmPlan(add = fetched.filter { it.id !in held }, cancel = cancel, keep = keep)
    }

    /** For the server: up to when the phone has each schedule (its push is then skipped). */
    fun ack(scheduled: List<PhoneAlarm>): JSONObject {
        val until = JSONObject()
        scheduled.filterNot { it.snooze }.groupBy { it.scheduleId }.forEach { (id, rings) ->
            until.put(id, rings.maxOf { it.at })
        }
        return JSONObject().put("until", until)
    }

    fun snoozed(alarm: PhoneAlarm, now: Long): PhoneAlarm =
        alarm.copy(id = "${alarm.scheduleId}~${now / 1000}", at = now + SNOOZE_MS, snooze = true)

    fun encode(alarms: List<PhoneAlarm>): String = JSONArray().apply {
        alarms.forEach {
            put(
                JSONObject()
                    .put("id", it.id).put("schedule_id", it.scheduleId).put("at", it.at)
                    .put("alarm", it.alarm).put("text", it.text).put("snooze", it.snooze),
            )
        }
    }.toString()

    fun decode(raw: String?): List<PhoneAlarm> {
        val list = runCatching { JSONArray(raw ?: return emptyList()) }.getOrNull() ?: return emptyList()
        return (0 until list.length()).mapNotNull { index ->
            val item = list.optJSONObject(index) ?: return@mapNotNull null
            PhoneAlarm(
                id = item.optString("id").ifEmpty { return@mapNotNull null },
                scheduleId = item.optString("schedule_id"),
                at = item.optLong("at"),
                alarm = item.optBoolean("alarm"),
                text = item.optString("text"),
                snooze = item.optBoolean("snooze"),
            )
        }
    }
}
