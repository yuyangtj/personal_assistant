package com.personalassistant.shell

import java.net.URI
import java.util.Locale

/** Where the console lives, and which links belong to it. */
object ConsoleUrls {
    /** "https://host[:port]" for an address, or null when it isn't an http(s) URL. */
    fun origin(url: String): String? = runCatching {
        val uri = URI(url.trim())
        val scheme = uri.scheme?.lowercase(Locale.ROOT)
        val host = uri.host?.lowercase(Locale.ROOT)
        if (scheme !in setOf("http", "https") || host.isNullOrBlank()) return null
        if (uri.port == -1) "$scheme://$host" else "$scheme://$host:${uri.port}"
    }.getOrNull()

    fun sameOrigin(a: String, b: String): Boolean = origin(a)?.let { it == origin(b) } ?: false

    /** A typed server address as an origin; "assistant.example.com" means https. */
    fun normalize(input: String): String? {
        val trimmed = input.trim()
        if (trimmed.isEmpty()) return null
        val withScheme = if ("://" in trimmed) trimmed else "https://$trimmed"
        return origin(withScheme)
    }

    /** The page to open: a link into the console as given (push links), else the console. */
    fun startUrl(server: String, link: String?): String {
        if (link != null && sameOrigin(server, link)) return link
        return "${origin(server) ?: server.trimEnd('/')}/ui"
    }
}
