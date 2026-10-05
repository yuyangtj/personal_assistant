package com.personalassistant.shell

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

class BridgeProtocolTest {
    @Test
    fun onlyKnownRequestsFromThePageAreAccepted() {
        assertEquals("route", BridgeProtocol.parse("""{"type":"route","id":"r1","text":"hi"}""")?.type)
        assertNull(BridgeProtocol.parse("""{"type":"run_intent","uri":"tel:123"}"""))
        assertNull(BridgeProtocol.parse("not json"))
        val long = BridgeProtocol.parse("""{"type":"speak","text":"${"a".repeat(5_000)}"}""")
        assertEquals(BridgeProtocol.MAX_TEXT, long?.text?.length)
    }

    @Test
    fun aConfirmRouteCarriesOnlyALabelAndTheAppsActionId() {
        val action = LocalAction(kind = LocalActionKind.TIMER, label = "Set a 5 minute timer", seconds = 300)

        val message = JSONObject(BridgeProtocol.route("r1", RequestRoute.Confirm(action), "a-1"))

        assertEquals("confirm", message.getString("route"))
        assertEquals("Set a 5 minute timer", message.getString("label"))
        assertEquals("a-1", message.getString("action"))
        assertFalse(message.has("seconds"))  // the page never holds the action itself
    }

    @Test
    fun repliesAndCloudRoutesAreDistinguished() {
        assertEquals("reply", JSONObject(BridgeProtocol.route("r", RequestRoute.Reply("Hi"))).getString("route"))
        assertEquals("cloud", JSONObject(BridgeProtocol.route("r", RequestRoute.Cloud("x"))).getString("route"))
    }

    @Test
    fun contextFromOtherAppsIsCappedAndTheSettingsRequestIsAccepted() {
        val screen = org.json.JSONObject(BridgeProtocol.screen("Willys", "x".repeat(20_000)))
        val shared = org.json.JSONObject(BridgeProtocol.shared("y".repeat(10_000)))
        val file = org.json.JSONObject(BridgeProtocol.sharedFile("klarna.csv", "date,merchant"))

        org.junit.Assert.assertEquals("screen", screen.getString("type"))
        org.junit.Assert.assertEquals(BridgeProtocol.MAX_SCREEN_TEXT, screen.getString("text").length)
        org.junit.Assert.assertEquals(BridgeProtocol.MAX_TEXT, shared.getString("text").length)
        org.junit.Assert.assertEquals("klarna.csv", file.getString("name"))
        org.junit.Assert.assertEquals(
            "open_assistant_settings",
            BridgeProtocol.parse("""{"type":"open_assistant_settings"}""")?.type,
        )
    }
}
