package com.personalassistant.shell

import java.time.Instant
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class OnDeviceRouterTest {
    private lateinit var router: OnDeviceRouter

    @Before
    fun setUp() {
        // The model is lazy, so parser tests never touch Android or AICore.
        router = OnDeviceRouter()
    }

    @After
    fun tearDown() = router.close()

    @Test
    fun deterministicTimerStaysOnDeviceAndRequiresConfirmation() {
        val result = router.deterministicRoute("Set a timer for 12 minutes")
        assertTrue(result is RequestRoute.Confirm)
        result as RequestRoute.Confirm
        assertEquals(LocalActionKind.TIMER, result.action.kind)
        assertEquals(720, result.action.seconds)
    }

    @Test
    fun modelReplyIsAcceptedFromFencedJson() {
        val result = router.parseModelResponse("```json\n{\"route\":\"reply\",\"reply\":\"Four.\"}\n```")
        assertTrue(result is RequestRoute.Reply)
        result as RequestRoute.Reply
        assertEquals("Four.", result.text)
    }

    @Test
    fun invalidTimerCannotReachAndroid() {
        assertNull(router.parseModelResponse("{\"route\":\"timer\",\"seconds\":999999}"))
    }

    @Test
    fun calendarMustBeFutureAndShort() {
        val now = Instant.parse("2026-10-03T10:00:00Z")
        val result = router.parseModelResponse(
            """{"route":"calendar","title":"Lunch","start":"2026-10-04T12:00:00+02:00","end":"2026-10-04T13:00:00+02:00"}""",
            now,
        )
        assertTrue(result is RequestRoute.Confirm)
        result as RequestRoute.Confirm
        assertEquals(LocalActionKind.CALENDAR, result.action.kind)
        assertEquals("Lunch", result.action.title)

        assertNull(
            router.parseModelResponse(
                """{"route":"calendar","title":"Past","start":"2026-10-02T12:00:00+02:00","end":"2026-10-02T13:00:00+02:00"}""",
                now,
            ),
        )
    }
}
