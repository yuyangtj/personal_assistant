package com.personalassistant.shell

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

class BridgeProtocolTest {
    @Test
    fun onlyKnownRequestsFromThePageAreAccepted() {
        assertEquals("listen", BridgeProtocol.parse("""{"type":"listen"}""")?.type)
        // The phone no longer decides what a message means: there is no routing to ask for.
        assertNull(BridgeProtocol.parse("""{"type":"route","id":"r1","text":"hi"}"""))
        assertNull(BridgeProtocol.parse("""{"type":"enable_on_device"}"""))
        assertNull(BridgeProtocol.parse("""{"type":"run_intent","uri":"tel:123"}"""))
        assertNull(BridgeProtocol.parse("not json"))
        val long = BridgeProtocol.parse("""{"type":"speak","text":"${"a".repeat(5_000)}"}""")
        assertEquals(BridgeProtocol.MAX_TEXT, long?.text?.length)
    }

    @Test
    fun aCheckedActionCarriesOnlyALabelAndTheAppsActionId() {
        val message = JSONObject(BridgeProtocol.messageAction("b1", "a-1", "Set a timer for 5 minutes", null))

        assertEquals("message_action", message.getString("type"))
        assertEquals("Set a timer for 5 minutes", message.getString("label"))
        assertEquals("a-1", message.getString("action"))
        assertFalse(message.has("seconds"))  // the page never holds the action itself
    }

    @Test
    fun aReplyCanBeSpokenASentenceAtATime() {
        assertEquals("speak_more", BridgeProtocol.parse("""{"type":"speak_more","text":"Then tea."}""")?.type)
        val caps = JSONObject(BridgeProtocol.capabilities(voice = true))
        assertEquals(true, caps.getBoolean("speakQueue"))
    }

    @Test
    fun capabilitiesNoLongerMentionAnOnDeviceModel() {
        val caps = JSONObject(BridgeProtocol.capabilities(voice = true, assistant = false))

        assertEquals(true, caps.getBoolean("voice"))
        assertFalse(caps.has("onDevice"))
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
