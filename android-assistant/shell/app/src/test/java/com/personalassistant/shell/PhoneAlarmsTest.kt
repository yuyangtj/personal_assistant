package com.personalassistant.shell

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PhoneAlarmsTest {
    private val now = 1_800_000_000_000L
    private val hour = 60L * 60 * 1000

    private fun body(vararg items: String) = JSONObject("""{"alarms":[${items.joinToString(",")}]}""")
    private fun item(id: String, at: Long, kind: String = "alarm", text: String = "Wake up") =
        """{"schedule_id":"$id","at":$at,"kind":"$kind","text":"$text"}"""

    @Test
    fun theServersListBecomesRingsAndAnythingOffIsLeftOut() {
        val alarms = PhoneAlarms.parse(
            body(
                item("a1", now + hour),
                item("r1", now + 2 * hour, kind = "reminder", text = "Call mum"),
                item("past", now - hour),
                item("far", now + 40L * 24 * hour),
                item("bad id!", now + hour),
                item("odd", now + hour, kind = "routine"),
                item("empty", now + hour, text = " "),
            ),
            now,
        )
        assertEquals(listOf("a1", "r1"), alarms.map { it.scheduleId })
        assertEquals(PhoneAlarm("a1@${(now + hour) / 1000}", "a1", now + hour, true, "Wake up"), alarms[0])
        assertEquals(false, alarms[1].alarm)
    }

    @Test
    fun syncingAddsNewCancelsGoneAndKeepsSnoozes() {
        val keep = PhoneAlarm("a@1", "a", now + hour, true, "Wake up")
        val gone = PhoneAlarm("b@1", "b", now + hour, false, "Deleted on the desktop")
        val snooze = PhoneAlarms.snoozed(keep, now)
        val rang = PhoneAlarm("c@1", "c", now - 1, false, "Already rang")
        val new = PhoneAlarm("d@1", "d", now + 3 * hour, false, "New")

        val plan = PhoneAlarms.plan(listOf(keep, gone, snooze, rang), listOf(keep, new), now)

        assertEquals(listOf(new), plan.add)
        assertEquals(listOf(gone, rang), plan.cancel)
        assertEquals(listOf(keep, snooze), plan.keep)
        assertEquals(now + PhoneAlarms.SNOOZE_MS, snooze.at)
        assertTrue(snooze.snooze)
    }

    @Test
    fun theServerLearnsUpToWhenThePhoneHasEachSchedule() {
        val first = PhoneAlarm("a@1", "a", now + hour, true, "Wake up")
        val second = first.copy(id = "a@2", at = now + 25 * hour)
        val snooze = PhoneAlarms.snoozed(first, now)
        val ack = PhoneAlarms.ack(listOf(first, second, snooze)).getJSONObject("until")
        assertEquals(now + 25 * hour, ack.getLong("a"))
        assertEquals(1, ack.length())
    }

    @Test
    fun storedRingsSurviveARoundTrip() {
        val alarms = listOf(
            PhoneAlarm("a@1", "a", now + hour, true, "Wake “up”"),
            PhoneAlarm("b~1", "b", now + hour, false, "Snoozed", snooze = true),
        )
        assertEquals(alarms, PhoneAlarms.decode(PhoneAlarms.encode(alarms)))
        assertEquals(emptyList<PhoneAlarm>(), PhoneAlarms.decode("not json"))
    }
}
