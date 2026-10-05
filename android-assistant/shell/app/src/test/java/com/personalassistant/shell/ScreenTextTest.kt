package com.personalassistant.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ScreenTextTest {
    private data class FakeNode(
        override val text: CharSequence? = null,
        override val contentDescription: CharSequence? = null,
        override val isPassword: Boolean = false,
        override val isVisible: Boolean = true,
        override val children: List<ScreenText.Node> = emptyList(),
    ) : ScreenText.Node

    @Test
    fun readsVisibleTextOnceAndSkipsPasswords() {
        val screen = FakeNode(
            children = listOf(
                FakeNode(text = "Zoégas  Skånerost\n450 g"),
                FakeNode(text = "54,90 kr"),
                FakeNode(contentDescription = "Add to cart"),
                FakeNode(text = "54,90 kr"),
                FakeNode(text = "hunter2", isPassword = true),
                FakeNode(text = "Hidden", isVisible = false, children = listOf(FakeNode(text = "Inside"))),
            ),
        )

        assertEquals("Zoégas Skånerost 450 g\n54,90 kr\nAdd to cart", ScreenText.collect(screen))
    }

    @Test
    fun staysWithinTheLimit() {
        val screen = FakeNode(children = (1..500).map { FakeNode(text = "Line number $it") })

        val text = ScreenText.collect(screen, limit = 100)

        assertTrue(text.length <= 100)
        assertTrue(text.startsWith("Line number 1\n"))
    }
}
