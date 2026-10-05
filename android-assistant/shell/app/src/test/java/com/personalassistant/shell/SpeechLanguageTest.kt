package com.personalassistant.shell

import java.util.Locale
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SpeechLanguageTest {
    private val englishSweden = Locale.forLanguageTag("en-SE")

    @Test
    fun englishInSwedenUsesAnEnglishThatHasAModel() {
        assertEquals("en-US", SpeechLanguage.choose(englishSweden, listOf("sv-SE", "en-GB", "en-US")))
        assertEquals("en-GB", SpeechLanguage.choose(englishSweden, listOf("en-GB", "en-IN")))
        assertEquals("en-IN", SpeechLanguage.choose(englishSweden, listOf("sv-SE", "en-IN")))
    }

    @Test
    fun anExactMatchWins() {
        assertEquals("en-IN", SpeechLanguage.choose(Locale.forLanguageTag("en-IN"), listOf("en-US", "en-IN")))
    }

    @Test
    fun anotherLanguageIsNeverChosen() {
        assertNull(SpeechLanguage.choose(englishSweden, listOf("sv-SE", "de-DE")))
    }
}
