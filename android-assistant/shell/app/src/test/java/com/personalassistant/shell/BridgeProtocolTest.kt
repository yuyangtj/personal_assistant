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
}
