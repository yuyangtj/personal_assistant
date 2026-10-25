package com.personalassistant.shell

import java.time.Instant
import java.time.LocalDate

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
