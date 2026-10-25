package com.personalassistant.shell

import java.time.Instant
import java.time.ZoneId
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ServerActionsTest {
    private val zone = ZoneId.of("Europe/Stockholm")
    private val now = Instant.parse("2026-10-25T10:00:00Z")

    private fun local(json: String) = ServerActions.toLocal(JSONObject(json), zone, now)

    @Test
    fun timersAlarmsAndEventsFromTheServerBecomeLocalActions() {
        val timer = local("""{"type":"set_timer","seconds":600,"label":"pasta"}""")!!
        assertEquals(LocalActionKind.TIMER, timer.kind)
        assertEquals("Set a timer for 10 minutes", timer.label)

        val alarm = local("""{"type":"set_alarm","hour":6,"minute":30,"days":[],"date":"2026-10-26"}""")!!
        assertEquals("Set an alarm for 06:30", alarm.label)
        assertEquals(java.time.LocalDate.parse("2026-10-26"), alarm.date)

        val weekly = local("""{"type":"set_alarm","hour":7,"minute":0,"days":[2,3,4,5,6],"date":null}""")!!
        assertEquals(listOf(2, 3, 4, 5, 6), weekly.days)

        val event = local("""{"type":"create_event","title":"Lunch with Anna","start":"2026-10-30T12:00","end":null,"location":""}""")!!
        assertEquals("Draft “Lunch with Anna” in Calendar", event.label)
        assertEquals(Instant.parse("2026-10-30T11:00:00Z"), event.start)  // 12:00 in Stockholm
        assertEquals(Instant.parse("2026-10-30T12:00:00Z"), event.end)  // an hour by default
    }

    @Test
    fun anythingOffIsRefused() {
        listOf(
            """{"type":"set_timer","seconds":0}""",
            """{"type":"set_timer","seconds":90000}""",
            """{"type":"set_alarm","hour":24,"minute":0,"date":"2026-10-26"}""",
            """{"type":"set_alarm","hour":7,"minute":0}""",  // neither a date nor days
            """{"type":"set_alarm","hour":7,"minute":0,"days":[8]}""",
            """{"type":"set_alarm","hour":7,"minute":0,"date":"2026-10-01"}""",  // passed
            """{"type":"create_event","title":"","start":"2026-10-30T12:00"}""",
            """{"type":"create_event","title":"Old","start":"2026-01-01T12:00"}""",
            """{"type":"create_event","title":"Long","start":"2026-10-30T12:00","end":"2026-11-02T12:00"}""",
            """{"type":"send_sms","to":"123"}""",
        ).forEach { assertNull(it, local(it)) }
    }

    @Test
    fun onlyWellFormedIdsMakeAPath() {
        assertEquals(
            "/chat-sessions/c-1/messages/m-2/phone-actions/b-3",
            ServerActions.path("c-1/m-2", "b-3"),
        )
        assertNull(ServerActions.path("c-1/../admin", "b-3"))
        assertNull(ServerActions.path("c-1/m-2/x", "b-3"))
        assertNull(ServerActions.path("c-1/m-2", "b/3"))
    }
}
