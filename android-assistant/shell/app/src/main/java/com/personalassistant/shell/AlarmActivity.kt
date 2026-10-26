package com.personalassistant.shell

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.util.TypedValue
import android.view.Gravity
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import java.text.DateFormat
import java.util.Date

/** A ringing alarm, full screen and over the lock screen: Snooze or Dismiss. */
class AlarmActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val alarm = AlarmReceiver.alarmFrom(intent) ?: return finish()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON,
            )
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        fun text(value: String, size: Float, color: Int = Color.WHITE) = TextView(this).apply {
            text = value
            setTextSize(TypedValue.COMPLEX_UNIT_SP, size)
            setTextColor(color)
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, 32)
        }
        fun button(label: String, onClick: () -> Unit) = Button(this).apply {
            text = label
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 20f)
            setOnClickListener { onClick(); finish() }
        }
        val buttons = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            val weight = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply {
                setMargins(24, 0, 24, 0)
            }
            addView(button("Snooze 10 min") { AlarmReceiver.snooze(this@AlarmActivity, alarm) }, weight)
            addView(button("Dismiss") { AlarmReceiver.dismiss(this@AlarmActivity, alarm) }, weight)
        }
        setContentView(
            LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.CENTER
                setBackgroundColor(Color.parseColor("#071116"))
                setPadding(48, 48, 48, 48)
                addView(text(DateFormat.getTimeInstance(DateFormat.SHORT).format(Date(alarm.at)), 64f))
                addView(text(alarm.text, 24f, Color.parseColor("#c9d6db")))
                addView(buttons)
            },
        )
    }

    companion object {
        fun intent(context: Context, alarm: PhoneAlarm): Intent =
            AlarmReceiver.intent(context, "", alarm)
                .setClass(context, AlarmActivity::class.java)
                .setAction(null)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_USER_ACTION)
    }
}
