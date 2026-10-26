package com.personalassistant.shell

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.net.Uri
import android.os.Build
import java.text.DateFormat
import java.util.Date
import kotlin.concurrent.thread

/**
 * Rings a reminder or alarm when AlarmManager fires it, and handles Snooze and Dismiss.
 *
 * An alarm rings like the Clock's: the alarm sound, repeating until dismissed (at most ten
 * minutes), full screen over the lock screen ([AlarmActivity]). A reminder is a heads-up
 * notification with the notification sound. Afterwards the app syncs, so a repeating one
 * gets its next time even if the app isn't opened.
 */
class AlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val alarm = alarmFrom(intent) ?: return
        when (intent.action) {
            ACTION_RING -> {
                ring(context, alarm)
                AlarmScheduler(context).forget(alarm.id)
                val pending = goAsync()
                thread {
                    try {
                        AlarmSync.sync(context)
                    } finally {
                        pending.finish()
                    }
                }
            }
            ACTION_SNOOZE -> snooze(context, alarm)
            ACTION_DISMISS -> dismiss(context, alarm)
        }
    }

    companion object {
        const val ACTION_RING = "com.personalassistant.shell.alarm.RING"
        const val ACTION_SNOOZE = "com.personalassistant.shell.alarm.SNOOZE"
        const val ACTION_DISMISS = "com.personalassistant.shell.alarm.DISMISS"
        private const val CHANNEL_ALARMS = "alarms"
        private const val CHANNEL_REMINDERS = "reminders"
        private const val RING_FOR_MS = 10L * 60 * 1000

        fun intent(context: Context, action: String, alarm: PhoneAlarm): Intent =
            Intent(context, AlarmReceiver::class.java)
                .setAction(action)
                .putExtra(EXTRA_ALARM, PhoneAlarms.encode(listOf(alarm)))

        fun alarmFrom(intent: Intent): PhoneAlarm? =
            PhoneAlarms.decode(intent.getStringExtra(EXTRA_ALARM)).firstOrNull()

        fun snooze(context: Context, alarm: PhoneAlarm) {
            notifications(context).cancel(alarm.id.hashCode())
            AlarmScheduler(context).add(PhoneAlarms.snoozed(alarm, System.currentTimeMillis()))
        }

        fun dismiss(context: Context, alarm: PhoneAlarm) {
            notifications(context).cancel(alarm.id.hashCode())
        }

        private const val EXTRA_ALARM = "alarm"

        private fun notifications(context: Context) =
            context.getSystemService(NotificationManager::class.java)

        private fun ring(context: Context, alarm: PhoneAlarm) {
            val manager = notifications(context)
            ensureChannels(context, manager)
            val open = PendingIntent.getActivity(
                context, 0, Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
            )
            val time = DateFormat.getTimeInstance(DateFormat.SHORT).format(Date(alarm.at))
            val builder = Notification.Builder(context, if (alarm.alarm) CHANNEL_ALARMS else CHANNEL_REMINDERS)
                .setSmallIcon(R.drawable.ic_tile_assistant)
                .setContentTitle(if (alarm.alarm) "Alarm · $time" else "Reminder")
                .setContentText(alarm.text)
                .setStyle(Notification.BigTextStyle().bigText(alarm.text))
                .setContentIntent(open)
                .setCategory(if (alarm.alarm) Notification.CATEGORY_ALARM else Notification.CATEGORY_REMINDER)
            if (alarm.alarm) {
                val screen = PendingIntent.getActivity(
                    context,
                    alarm.id.hashCode(),
                    AlarmActivity.intent(context, alarm),
                    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
                )
                builder.setFullScreenIntent(screen, true)
                    .setOngoing(true)
                    .setTimeoutAfter(RING_FOR_MS)
                    .addAction(action(context, ACTION_SNOOZE, alarm, "Snooze 10 min"))
                    .addAction(action(context, ACTION_DISMISS, alarm, "Dismiss"))
            } else {
                builder.setAutoCancel(true)
                    .addAction(action(context, ACTION_SNOOZE, alarm, "Snooze 10 min"))
            }
            val notification = builder.build()
            if (alarm.alarm) notification.flags = notification.flags or Notification.FLAG_INSISTENT
            runCatching { manager.notify(alarm.id.hashCode(), notification) }
        }

        private fun action(context: Context, action: String, alarm: PhoneAlarm, label: String) =
            Notification.Action.Builder(
                null,
                label,
                PendingIntent.getBroadcast(
                    context,
                    0,
                    intent(context, action, alarm).setData(Uri.fromParts("phonealarm", "$action/${alarm.id}", null)),
                    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
                ),
            ).build()

        private fun ensureChannels(context: Context, manager: NotificationManager) {
            val alarms = NotificationChannel(CHANNEL_ALARMS, "Alarms", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Alarms you set with the assistant"
                setSound(
                    RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM),
                    AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build(),
                )
                enableVibration(true)
                lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            }
            val reminders = NotificationChannel(
                CHANNEL_REMINDERS, "Reminders", NotificationManager.IMPORTANCE_HIGH,
            ).apply { description = "Reminders you set with the assistant" }
            manager.createNotificationChannels(listOf(alarms, reminders))
        }
    }
}

/** Android drops every alarm on a reboot (and an app update): set the held ones again. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action in setOf(Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED)) {
            AlarmScheduler(context).restore()
        }
    }
}
