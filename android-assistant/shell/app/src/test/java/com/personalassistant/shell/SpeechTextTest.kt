package com.personalassistant.shell

import org.junit.Assert.assertEquals
import org.junit.Test

class SpeechTextTest {
    @Test
    fun inlineLinksAreReadAsTheLink() {
        assertEquals(
            "To review PR #35, open the link in your browser.",
            SpeechText.speakable("To review PR #35, open https://github.com/yuyangtj/personal_assistant/pull/35 in your browser."),
        )
    }

    @Test
    fun sourceListsAndMarkdownAreNotRead() {
        val reply = "Two good desks:\n- **Flexispot E7**, sturdy\n- Ikea Rodulf\n\nSources:\nhttps://a.example/x\n- https://b.example/y"
        assertEquals("Two good desks:\nFlexispot E7, sturdy\nIkea Rodulf", SpeechText.speakable(reply))
    }

    @Test
    fun plainTextIsUnchanged() {
        assertEquals("It’s 10:14.", SpeechText.speakable("It’s 10:14."))
    }
}
