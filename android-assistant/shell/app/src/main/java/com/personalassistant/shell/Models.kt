package com.personalassistant.shell

import java.time.Instant
import java.time.LocalDate

enum class DeviceAiAvailability(val wire: String) {
    CHECKING("checking"),
    READY("ready"),
    DOWNLOADABLE("downloadable"),
    DOWNLOADING("downloading"),
    UNAVAILABLE("unavailable"),
    ERROR("error"),
}

data class DeviceAiState(
    val availability: DeviceAiAvailability = DeviceAiAvailability.CHECKING,
    val detail: String = "Checking on-device AI…",
    val progress: Float? = null,
)

enum class LocalActionKind { TIMER, ALARM, CALENDAR }

data class LocalAction(
    val kind: LocalActionKind,
    val label: String,
    val seconds: Int? = null,
    val hour: Int? = null,
    val minute: Int? = null,
    val date: LocalDate? = null,
    val days: List<Int> = emptyList(),
    val title: String? = null,
    val start: Instant? = null,
    val end: Instant? = null,
    val location: String = "",
)

sealed interface RequestRoute {
    data class Reply(val text: String) : RequestRoute
    data class Confirm(val action: LocalAction) : RequestRoute
    data class Cloud(val reason: String) : RequestRoute
}
