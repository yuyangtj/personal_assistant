package com.personalassistant.shell

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build

/**
 * The reminders and alarms this phone holds, set with Android's AlarmManager so they ring
 * at the exact time even when the app is closed, and kept in preferences so a reboot can
 * set them again ([BootReceiver]).
 */
class AlarmScheduler(private val context: Context) {
    private val preferences = context.getSharedPreferences("phone_alarms", Context.MODE_PRIVATE)
    private val manager = context.getSystemService(AlarmManager::class.java)

    fun stored(): List<PhoneAlarm> = PhoneAlarms.decode(preferences.getString(KEY, null))

    private fun save(alarms: List<PhoneAlarm>) {
        preferences.edit().putString(KEY, PhoneAlarms.encode(alarms)).apply()
    }

    @Synchronized
    fun apply(plan: AlarmPlan) {
        plan.cancel.forEach(::cancel)
        plan.add.forEach(::set)
        save(plan.all)
    }

    /** One more, the phone's own (a snooze). */
    @Synchronized
    fun add(alarm: PhoneAlarm) {
        set(alarm)
        save(stored().filterNot { it.id == alarm.id } + alarm)
    }

    /** It rang (or was dismissed): no longer held. */
    @Synchronized
    fun forget(id: String) {
        save(stored().filterNot { it.id == id })
    }

    /** After a reboot or an app update Android has dropped them all: set the future ones again. */
    @Synchronized
    fun restore(now: Long = System.currentTimeMillis()) {
        val future = stored().filter { it.at > now }
        future.forEach(::set)
        save(future)
    }

    private fun set(alarm: PhoneAlarm) {
        val ring = ringIntent(alarm)
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && !manager.canScheduleExactAlarms()) {
                manager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, alarm.at, ring)
            } else if (alarm.alarm) {
                // Shown in the status bar and on the lock screen, like the Clock's own alarms.
                val show = PendingIntent.getActivity(
                    context, 0, Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
                )
                manager.setAlarmClock(AlarmManager.AlarmClockInfo(alarm.at, show), ring)
            } else {
                manager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, alarm.at, ring)
            }
        } catch (_: SecurityException) {
            manager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, alarm.at, ring)
        }
    }

    private fun cancel(alarm: PhoneAlarm) {
        manager.cancel(ringIntent(alarm))
    }

    private fun ringIntent(alarm: PhoneAlarm): PendingIntent = PendingIntent.getBroadcast(
        context,
        0,
        AlarmReceiver.intent(context, AlarmReceiver.ACTION_RING, alarm)
            // The id in the data keeps each ring its own PendingIntent.
            .setData(Uri.fromParts("phonealarm", alarm.id, null)),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private companion object {
        const val KEY = "alarms"
    }
}
