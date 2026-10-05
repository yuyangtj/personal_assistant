package com.personalassistant.shell

/** Turns a written reply into something worth hearing: no URLs, no markdown marks. */
object SpeechText {
    private val url = Regex("""https?://\S+""")
    private val bullet = Regex("""^\s*(?:[-*•]|\d+[.)])\s+""")
    private val emphasis = Regex("""\*\*|__|`""")

    fun speakable(text: String): String {
        val lines = text.lines()
            .map { line -> line.replace(emphasis, "") }
            // A line that is only a link (a source list) says nothing when read out.
            .filterNot { line -> line.replace(bullet, "").trim().let { it.isNotEmpty() && url.matches(it) } }
            .map { line -> line.replace(bullet, "").replace(url) { "the link" }.trim() }
            .filter { it.isNotEmpty() }
        return lines
            // A heading such as "Sources:" with nothing after it is dropped too.
            .filterIndexed { index, line -> !(line.endsWith(":") && index == lines.lastIndex) }
            .joinToString("\n")
            .replace(Regex("""\(\s*the link\s*\)"""), "")
            .replace(Regex(""" {2,}"""), " ")
            .trim()
    }
}
