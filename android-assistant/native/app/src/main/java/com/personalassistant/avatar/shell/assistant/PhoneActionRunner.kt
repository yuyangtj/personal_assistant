package com.personalassistant.avatar.shell.assistant

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.provider.AlarmClock
import android.provider.CalendarContract
import java.time.ZoneId
import java.util.Calendar

/** Runs confirmed actions through the phone's own Clock and Calendar apps via public intents. */
class PhoneActionRunner(private val activity: Activity) {
    /** Returns null on success, or a user-facing reason on failure. */
    fun run(action: PhoneAction): String? {
        val intent = when (action) {
            is PhoneAction.SetTimer -> Intent(AlarmClock.ACTION_SET_TIMER)
                .putExtra(AlarmClock.EXTRA_LENGTH, action.seconds)
                .putExtra(AlarmClock.EXTRA_MESSAGE, action.label)
                .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
            is PhoneAction.SetAlarm -> Intent(AlarmClock.ACTION_SET_ALARM)
                .putExtra(AlarmClock.EXTRA_HOUR, action.hour)
                .putExtra(AlarmClock.EXTRA_MINUTES, action.minute)
                .putExtra(AlarmClock.EXTRA_MESSAGE, action.label)
                .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
                .apply {
                    if (action.days.isNotEmpty()) {
                        putIntegerArrayListExtra(AlarmClock.EXTRA_DAYS, ArrayList(action.days.filter { it in Calendar.SUNDAY..Calendar.SATURDAY }))
                    }
                }
            is PhoneAction.CreateEvent -> {
                val zone = ZoneId.systemDefault()
                val begin = action.start.atZone(zone).toInstant().toEpochMilli()
                val end = (action.end ?: action.start.plusHours(1)).atZone(zone).toInstant().toEpochMilli()
                Intent(Intent.ACTION_INSERT, CalendarContract.Events.CONTENT_URI)
                    .putExtra(CalendarContract.Events.TITLE, action.title)
                    .putExtra(CalendarContract.EXTRA_EVENT_BEGIN_TIME, begin)
                    .putExtra(CalendarContract.EXTRA_EVENT_END_TIME, end)
                    .putExtra(CalendarContract.Events.EVENT_LOCATION, action.location)
            }
        }
        return try {
            activity.startActivity(intent)
            null
        } catch (_: ActivityNotFoundException) {
            when (action) {
                is PhoneAction.CreateEvent -> "No calendar app is available on this phone."
                else -> "No clock app on this phone accepts timers or alarms."
            }
        } catch (_: SecurityException) {
            "The phone did not allow that action."
        }
    }
}
