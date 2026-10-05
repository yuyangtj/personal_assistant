package com.personalassistant.shell

/**
 * The readable text of the screen the user was on when they opened the assistant.
 *
 * Built from Android's assist structure (only provided when "Use text from screen" is on,
 * and never for apps that mark their windows secure, such as banking apps). Password
 * fields are skipped; repeated lines are dropped; the result is capped.
 */
object ScreenText {
    const val MAX_CHARACTERS = 4_000

    /** One node of the screen's view tree, as far as reading it goes. */
    interface Node {
        val text: CharSequence?
        val contentDescription: CharSequence?
        val isPassword: Boolean
        val isVisible: Boolean
        val children: List<Node>
    }

    fun collect(root: Node, limit: Int = MAX_CHARACTERS): String {
        val lines = LinkedHashSet<String>()
        fun walk(node: Node) {
            if (!node.isVisible || node.isPassword) return
            val line = (node.text ?: node.contentDescription)
                ?.toString()
                ?.replace(Regex("\\s+"), " ")
                ?.trim()
            if (!line.isNullOrEmpty()) lines.add(line)
            node.children.forEach(::walk)
        }
        walk(root)
        val text = StringBuilder()
        for (line in lines) {
            if (text.length + line.length + 1 > limit) break
            if (text.isNotEmpty()) text.append('\n')
            text.append(line)
        }
        return text.toString()
    }
}
