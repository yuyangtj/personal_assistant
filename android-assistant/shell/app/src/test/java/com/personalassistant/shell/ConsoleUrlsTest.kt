package com.personalassistant.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ConsoleUrlsTest {
    @Test
    fun originKeepsSchemeHostAndExplicitPortOnly() {
        assertEquals("https://assistant.example.com", ConsoleUrls.origin("https://Assistant.example.com/ui#/chats"))
        assertEquals("http://10.0.2.2:8765", ConsoleUrls.origin("http://10.0.2.2:8765/ui"))
        assertNull(ConsoleUrls.origin("javascript:alert(1)"))
        assertNull(ConsoleUrls.origin("file:///sdcard/x.html"))
    }

    @Test
    fun onlyTheConsolesOwnOriginCountsAsInside() {
        val server = "https://assistant.example.com"
        assertTrue(ConsoleUrls.sameOrigin(server, "https://assistant.example.com/ui?task=1"))
        assertFalse(ConsoleUrls.sameOrigin(server, "https://github.com/acme/widget/pull/41"))
        assertFalse(ConsoleUrls.sameOrigin(server, "http://assistant.example.com/ui"))
        assertFalse(ConsoleUrls.sameOrigin(server, "https://assistant.example.com.evil.test/ui"))
    }

    @Test
    fun pushLinksOpenAsGivenAndEverythingElseOpensTheConsole() {
        val server = "https://assistant.example.com"
        assertEquals("https://assistant.example.com/?task=7", ConsoleUrls.startUrl(server, "https://assistant.example.com/?task=7"))
        assertEquals("https://assistant.example.com/ui", ConsoleUrls.startUrl(server, null))
        assertEquals("https://assistant.example.com/ui", ConsoleUrls.startUrl(server, "https://other.test/x"))
    }

    @Test
    fun typedServerAddressesBecomeOrigins() {
        assertEquals("https://assistant.example.com", ConsoleUrls.normalize(" assistant.example.com/ui "))
        assertEquals("http://10.0.2.2:8765", ConsoleUrls.normalize("http://10.0.2.2:8765"))
        assertNull(ConsoleUrls.normalize(""))
    }
}
