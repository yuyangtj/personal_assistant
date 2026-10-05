package com.personalassistant.shell

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.provider.AlarmClock
import android.provider.CalendarContract
import java.time.ZonedDateTime
import java.util.Calendar

/** Executes only actions that have already been shown to and confirmed by the user. */
class PhoneActionRunner(private val activity: Activity) {
    fun run(action: LocalAction): String? {
        val intent = when (action.kind) {
            LocalActionKind.TIMER -> Intent(AlarmClock.ACTION_SET_TIMER)
                .putExtra(AlarmClock.EXTRA_LENGTH, requireNotNull(action.seconds))
                .putExtra(AlarmClock.EXTRA_MESSAGE, "Personal Assistant")
                .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
            LocalActionKind.ALARM -> Intent(AlarmClock.ACTION_SET_ALARM)
                .putExtra(AlarmClock.EXTRA_HOUR, requireNotNull(action.hour))
                .putExtra(AlarmClock.EXTRA_MINUTES, requireNotNull(action.minute))
                .putExtra(AlarmClock.EXTRA_MESSAGE, "Personal Assistant")
                .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
                .apply {
                    if (action.days.isNotEmpty()) {
                        putIntegerArrayListExtra(
                            AlarmClock.EXTRA_DAYS,
                            ArrayList(action.days.filter { it in Calendar.SUNDAY..Calendar.SATURDAY }),
                        )
                    }
                }
            LocalActionKind.CALENDAR -> Intent(Intent.ACTION_INSERT, CalendarContract.Events.CONTENT_URI)
                .putExtra(CalendarContract.Events.TITLE, requireNotNull(action.title))
                .putExtra(CalendarContract.EXTRA_EVENT_BEGIN_TIME, requireNotNull(action.start).toEpochMilli())
                .putExtra(CalendarContract.EXTRA_EVENT_END_TIME, requireNotNull(action.end).toEpochMilli())
                .putExtra(CalendarContract.Events.EVENT_LOCATION, action.location)
        }
        if (action.kind == LocalActionKind.ALARM && action.days.isEmpty()) {
            val now = ZonedDateTime.now()
            var next = now
                .withHour(requireNotNull(action.hour))
                .withMinute(requireNotNull(action.minute))
                .withSecond(0)
                .withNano(0)
            if (!next.isAfter(now)) next = next.plusDays(1)
            if (action.date != null && action.date != next.toLocalDate()) {
                return "That alarm time has passed. Please ask me to prepare it again."
            }
        }
        return try {
            activity.startActivity(intent)
            null
        } catch (_: ActivityNotFoundException) {
            when (action.kind) {
                LocalActionKind.CALENDAR -> "No calendar app is available on this phone."
                else -> "No clock app on this phone accepts this action."
            }
        } catch (_: SecurityException) {
            "The phone did not allow that action."
        }
    }
}
