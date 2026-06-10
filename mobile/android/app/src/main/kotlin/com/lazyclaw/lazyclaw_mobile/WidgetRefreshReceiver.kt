package com.lazyclaw.lazyclaw_mobile

import android.appwidget.AppWidgetManager
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent

/**
 * Repaints both home-screen widgets right after the app package is updated.
 *
 * OEM launchers (notably Xiaomi/HyperOS) routinely leave app widgets frozen on
 * their last RemoteViews — or the static initialLayout — after an APK update,
 * and the app's own HomeWidget.updateWidget broadcast only fires the next time
 * the app runs. Since this app self-updates via the in-app APK download, every
 * update risked a widget stuck showing nothing but its header until re-added.
 * ACTION_MY_PACKAGE_REPLACED is delivered exactly once per update, so a repaint
 * here closes that window without any polling.
 */
class WidgetRefreshReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        refresh(context, TasksWidget::class.java)
        refresh(context, QuickCaptureWidget::class.java)
    }

    private fun refresh(context: Context, cls: Class<*>) {
        try {
            val manager = AppWidgetManager.getInstance(context)
            val ids = manager.getAppWidgetIds(ComponentName(context, cls))
            if (ids.isEmpty()) return
            val update = Intent(context, cls).apply {
                action = AppWidgetManager.ACTION_APPWIDGET_UPDATE
                putExtra(AppWidgetManager.EXTRA_APPWIDGET_IDS, ids)
            }
            context.sendBroadcast(update)
        } catch (_: Throwable) {
            // A repaint failure must never crash the package-replaced path.
        }
    }
}
