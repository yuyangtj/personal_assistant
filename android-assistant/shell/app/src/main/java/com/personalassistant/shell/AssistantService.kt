package com.personalassistant.shell

import android.app.assist.AssistContent
import android.app.assist.AssistStructure
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService
import android.text.InputType
import android.view.View

/**
 * The app as the phone's digital assistant (long-press power, or a swipe from a corner).
 *
 * Android binds this service once the user picks the app under Default apps → Digital
 * assistant app. Each invocation opens a session, which reads the screen the user was on
 * (only when "Use text from screen" is allowed) and hands it to the console.
 */
class AssistantService : VoiceInteractionService()

class AssistantSessionService : VoiceInteractionSessionService() {
    override fun onNewSession(args: Bundle?): VoiceInteractionSession = AssistantSession(this)
}

class AssistantSession(context: Context) : VoiceInteractionSession(context) {
    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        // Without screen access the assist data never comes: open the console right away.
        if (showFlags and SHOW_WITH_ASSIST == 0) open(app = "", text = "")
    }

    @Deprecated("Replaced by onHandleAssist(AssistState), whose default calls this")
    override fun onHandleAssist(data: Bundle?, structure: AssistStructure?, content: AssistContent?) {
        if (structure == null) {
            open(app = "", text = "")  // a secure window (banking apps) shares nothing
            return
        }
        val text = (0 until structure.windowNodeCount)
            .map { ScreenText.collect(ViewNodeAdapter(structure.getWindowNodeAt(it).rootViewNode)) }
            .filter { it.isNotEmpty() }
            .joinToString("\n")
            .take(ScreenText.MAX_CHARACTERS)
        open(app = appLabel(structure), text = text)
    }

    private fun open(app: String, text: String) {
        startAssistantActivity(
            Intent(context, MainActivity::class.java)
                .setAction(MainActivity.ACTION_ASSIST)
                .putExtra(MainActivity.EXTRA_SCREEN_APP, app)
                .putExtra(MainActivity.EXTRA_SCREEN_TEXT, text)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
        hide()
    }

    private fun appLabel(structure: AssistStructure): String {
        val component: ComponentName = structure.activityComponent ?: return ""
        return try {
            val info = context.packageManager.getApplicationInfo(component.packageName, 0)
            context.packageManager.getApplicationLabel(info).toString()
        } catch (_: PackageManager.NameNotFoundException) {
            structure.takeIf { it.windowNodeCount > 0 }?.getWindowNodeAt(0)?.title?.toString()
                ?: component.packageName
        }
    }
}

/** An assist-structure view node, as [ScreenText] reads it. */
private class ViewNodeAdapter(private val node: AssistStructure.ViewNode) : ScreenText.Node {
    override val text: CharSequence? get() = node.text
    override val contentDescription: CharSequence? get() = node.contentDescription
    override val isVisible: Boolean get() = node.visibility == View.VISIBLE
    override val isPassword: Boolean
        get() {
            val type = node.inputType
            val variation = type and InputType.TYPE_MASK_VARIATION
            return when (type and InputType.TYPE_MASK_CLASS) {
                InputType.TYPE_CLASS_TEXT -> variation in TEXT_PASSWORDS
                InputType.TYPE_CLASS_NUMBER -> variation == InputType.TYPE_NUMBER_VARIATION_PASSWORD
                else -> false
            }
        }
    override val children: List<ScreenText.Node>
        get() = (0 until node.childCount).map { ViewNodeAdapter(node.getChildAt(it)) }

    private companion object {
        val TEXT_PASSWORDS = setOf(
            InputType.TYPE_TEXT_VARIATION_PASSWORD,
            InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD,
            InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD,
        )
    }
}
