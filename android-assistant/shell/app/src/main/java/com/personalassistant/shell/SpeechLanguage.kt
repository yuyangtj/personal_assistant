package com.personalassistant.shell

import java.util.Locale

/**
 * Which recognition language to ask for. The phone's own locale often has no speech model
 * (Google has none for "English (Sweden)"), and asking for it silently sends the voice to
 * Google's servers; the same language in a region that has a model keeps it on the phone.
 */
object SpeechLanguage {
    /** Regions tried first for a language whose exact locale has no model. */
    private val preferredRegions = mapOf("en" to listOf("US", "GB"), "sv" to listOf("SE"))

    fun choose(wanted: Locale, available: Collection<String>): String? {
        val tags = available.associateBy { it.lowercase(Locale.ROOT) }
        tags[wanted.toLanguageTag().lowercase(Locale.ROOT)]?.let { return it }
        val language = wanted.language.lowercase(Locale.ROOT)
        preferredRegions[language].orEmpty().forEach { region ->
            tags["$language-${region.lowercase(Locale.ROOT)}"]?.let { return it }
        }
        return available.firstOrNull { Locale.forLanguageTag(it).language == language }
    }
}
