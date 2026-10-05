package com.personalassistant.shell

import android.util.Log
import com.google.mlkit.genai.common.DownloadStatus
import com.google.mlkit.genai.common.FeatureStatus
import com.google.mlkit.genai.prompt.Generation
import com.google.mlkit.genai.prompt.GenerativeModel
import com.google.mlkit.genai.prompt.TextPart
import com.google.mlkit.genai.prompt.generateContentRequest
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale
import kotlin.math.roundToInt
import kotlinx.coroutines.flow.collect
import org.json.JSONObject

/**
 * Routes a deliberately small set of requests through Gemini Nano in AICore.
 *
 * Model output is untrusted. Only the schema parsed by [parseModelResponse] can become a local
 * reply or a proposed action, and all actions still require confirmation in the UI.
 */
class OnDeviceRouter {
    private var modelInstance: GenerativeModel? = null
    private val model: GenerativeModel
        get() = modelInstance ?: Generation.getClient().also { modelInstance = it }

    suspend fun checkStatus(): DeviceAiState = try {
        when (model.checkStatus()) {
            FeatureStatus.AVAILABLE -> DeviceAiState(
                DeviceAiAvailability.READY,
                "Gemini Nano is ready. Supported requests stay on this phone.",
            )
            FeatureStatus.DOWNLOADABLE -> DeviceAiState(
                DeviceAiAvailability.DOWNLOADABLE,
                "Gemini Nano is supported but its model is not downloaded.",
            )
            FeatureStatus.DOWNLOADING -> DeviceAiState(
                DeviceAiAvailability.DOWNLOADING,
                "Gemini Nano is already downloading.",
            )
            else -> DeviceAiState(
                DeviceAiAvailability.UNAVAILABLE,
                "AICore is not available on this device. Cloud fallback still works.",
            )
        }
    } catch (_: Throwable) {
        // Some device/AICore versions throw instead of returning UNAVAILABLE. Never crash the app.
        DeviceAiState(
            DeviceAiAvailability.UNAVAILABLE,
            "AICore is not available on this device. Cloud fallback still works.",
        )
    }

    suspend fun download(onState: (DeviceAiState) -> Unit) {
        try {
            var bytesToDownload = 0L
            model.download().collect { status ->
                when (status) {
                    is DownloadStatus.DownloadStarted -> {
                        bytesToDownload = status.bytesToDownload
                        onState(
                            DeviceAiState(
                                DeviceAiAvailability.DOWNLOADING,
                                "Downloading Gemini Nano…",
                                0f,
                            ),
                        )
                    }
                    is DownloadStatus.DownloadProgress -> {
                        val total = bytesToDownload.coerceAtLeast(1L)
                        val progress = status.totalBytesDownloaded.toFloat() / total.toFloat()
                        onState(
                            DeviceAiState(
                                DeviceAiAvailability.DOWNLOADING,
                                "Downloading Gemini Nano… ${(progress * 100).roundToInt()}%",
                                progress.coerceIn(0f, 1f),
                            ),
                        )
                    }
                    DownloadStatus.DownloadCompleted -> onState(
                        DeviceAiState(
                            DeviceAiAvailability.READY,
                            "Gemini Nano is ready. Supported requests stay on this phone.",
                            1f,
                        ),
                    )
                    is DownloadStatus.DownloadFailed -> onState(
                        DeviceAiState(
                            DeviceAiAvailability.ERROR,
                            "The on-device model download failed. You can try again later.",
                        ),
                    )
                }
            }
        } catch (_: Throwable) {
            onState(
                DeviceAiState(
                    DeviceAiAvailability.ERROR,
                    "The on-device model download failed. You can try again later.",
                ),
            )
        }
    }

    suspend fun route(request: String, availability: DeviceAiAvailability): RequestRoute {
        deterministicRoute(request)?.let { return it }
        if (availability != DeviceAiAvailability.READY) {
            return RequestRoute.Cloud("On-device AI is not ready")
        }

        return try {
            val now = ZonedDateTime.now().withNano(0)
            val result = model.generateContent(
                generateContentRequest(TextPart(routerPrompt(request, now))) {
                    temperature = 0f
                    topK = 1
                    candidateCount = 1
                    maxOutputTokens = 220
                },
            )
            val text = result.candidates.firstOrNull()?.text.orEmpty()
            parseModelResponse(text, now.toInstant())
                ?.also { Log.i(TAG, "AICORE_ROUTE type=${it.javaClass.simpleName}") }
                ?: RequestRoute.Cloud("The on-device result did not pass validation")
        } catch (error: Throwable) {
            Log.w(TAG, "AICORE_ROUTE_FAILED ${error.javaClass.simpleName}")
            RequestRoute.Cloud("On-device inference was unavailable")
        }
    }

    fun close() {
        modelInstance?.close()
        modelInstance = null
    }

    internal fun deterministicRoute(request: String): RequestRoute? {
        val normalized = request.trim().lowercase(Locale.ROOT)
        if (normalized in setOf("hi", "hello", "hey", "hello there", "hey there")) {
            return RequestRoute.Reply("Hello. What can I help with?")
        }
        if (Regex("\\b(what('?s| is) the time|current time|time is it)\\b").containsMatchIn(normalized)) {
            val time = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).format(ZonedDateTime.now())
            return RequestRoute.Reply("It’s $time.")
        }
        if (Regex("\\b(what('?s| is) (the )?date|what day is it|today('?s)? date)\\b").containsMatchIn(normalized)) {
            val date = DateTimeFormatter.ofLocalizedDate(FormatStyle.FULL).format(ZonedDateTime.now())
            return RequestRoute.Reply("Today is $date.")
        }

        val timer = TIMER_PATTERN.find(normalized)
        if (timer != null) {
            val amount = timer.groupValues[1].toIntOrNull() ?: return null
            val unit = timer.groupValues[2]
            val seconds = when {
                unit.startsWith("hour") -> amount * 3600
                unit.startsWith("minute") -> amount * 60
                else -> amount
            }
            if (seconds in 1..86_400) {
                return RequestRoute.Confirm(
                    LocalAction(
                        kind = LocalActionKind.TIMER,
                        label = "Set a timer for ${humanDuration(seconds)}",
                        seconds = seconds,
                    ),
                )
            }
        }
        return null
    }

    internal fun parseModelResponse(raw: String, now: Instant = Instant.now()): RequestRoute? {
        val start = raw.indexOf('{')
        val end = raw.lastIndexOf('}')
        if (start < 0 || end <= start) return null
        val payload = runCatching { JSONObject(raw.substring(start, end + 1)) }.getOrNull() ?: return null
        return when (payload.optString("route").lowercase(Locale.ROOT)) {
            "reply" -> payload.optString("reply").trim().takeIf { it.length in 1..600 }
                ?.let(RequestRoute::Reply)
            "timer" -> {
                val seconds = payload.optInt("seconds", -1)
                if (seconds !in 1..86_400) null else RequestRoute.Confirm(
                    LocalAction(
                        kind = LocalActionKind.TIMER,
                        label = "Set a timer for ${humanDuration(seconds)}",
                        seconds = seconds,
                    ),
                )
            }
            "alarm" -> {
                val hour = payload.optInt("hour", -1)
                val minute = payload.optInt("minute", -1)
                if (hour !in 0..23 || minute !in 0..59) null else RequestRoute.Confirm(
                    LocalAction(
                        kind = LocalActionKind.ALARM,
                        label = "Set an alarm for %02d:%02d".format(hour, minute),
                        hour = hour,
                        minute = minute,
                        date = nextAlarmDate(hour, minute),
                    ),
                )
            }
            "calendar" -> parseCalendar(payload, now)
            "cloud" -> RequestRoute.Cloud(payload.optString("reason", "This request needs the assistant core"))
            else -> null
        }
    }

    private fun parseCalendar(payload: JSONObject, now: Instant): RequestRoute? {
        val title = payload.optString("title").trim().take(80)
        val start = parseInstant(payload.optString("start")) ?: return null
        val end = parseInstant(payload.optString("end")) ?: return null
        val duration = Duration.between(start, end)
        val latest = now.plus(Duration.ofDays(366))
        if (
            title.isBlank() ||
            start.isBefore(now.minus(Duration.ofMinutes(5))) ||
            start.isAfter(latest) ||
            duration < Duration.ofMinutes(5) ||
            duration > Duration.ofHours(24)
        ) return null
        return RequestRoute.Confirm(
            LocalAction(
                kind = LocalActionKind.CALENDAR,
                label = "Draft “$title” in Calendar",
                title = title,
                start = start,
                end = end,
            ),
        )
    }

    private fun parseInstant(value: String): Instant? =
        runCatching { OffsetDateTime.parse(value).toInstant() }.getOrNull()

    private fun routerPrompt(request: String, now: ZonedDateTime): String = """
        You are the private on-device router for a personal assistant. Current local time is $now.
        Classify the user request and return exactly one compact JSON object, with no markdown.

        Allowed schemas:
        {"route":"reply","reply":"short answer, at most 80 words"}
        {"route":"timer","seconds":integer}
        {"route":"alarm","hour":0-23,"minute":0-59}
        {"route":"calendar","title":"text","start":"ISO-8601 with offset","end":"ISO-8601 with offset"}
        {"route":"cloud","reason":"short reason"}

        Use reply only for casual conversation, simple arithmetic, rewriting, or stable general
        knowledge that needs no current information, personal data, tools, or long reasoning.
        Use timer, alarm, or calendar only when the user explicitly asks for that single action and
        all required details are present. Use cloud for news, weather, prices, research, coding,
        messages, email, private data, multi-step work, ambiguous actions, medical/legal/financial
        advice, or anything you cannot answer reliably. Never claim an action has already happened.

        User request: ${request.take(1_200)}
    """.trimIndent()

    private companion object {
        val TIMER_PATTERN = Regex("(?:set|start)(?: me)?(?: a)? timer(?: for)? (\\d{1,4}) (seconds?|minutes?|hours?)")
        const val TAG = "AssistantShell"

        fun humanDuration(seconds: Int): String = when {
            seconds % 3600 == 0 -> "${seconds / 3600} hour" + if (seconds == 3600) "" else "s"
            seconds % 60 == 0 -> "${seconds / 60} minute" + if (seconds == 60) "" else "s"
            else -> "$seconds second" + if (seconds == 1) "" else "s"
        }

        fun nextAlarmDate(hour: Int, minute: Int): java.time.LocalDate {
            val now = ZonedDateTime.now()
            var next = now.withHour(hour).withMinute(minute).withSecond(0).withNano(0)
            if (!next.isAfter(now)) next = next.plusDays(1)
            return next.toLocalDate()
        }
    }
}
