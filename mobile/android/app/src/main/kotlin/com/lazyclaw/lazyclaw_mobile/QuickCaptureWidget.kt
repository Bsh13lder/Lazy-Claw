package com.lazyclaw.lazyclaw_mobile

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.net.Uri
import android.widget.RemoteViews
import es.antonborri.home_widget.HomeWidgetLaunchIntent
import es.antonborri.home_widget.HomeWidgetProvider

/**
 * LazyClaw Quick-Capture home-screen widget (4x1).
 *
 * Each of the four buttons launches the app with a `lazyclaw://<action>` deep
 * link. The Flutter side ([DeepLinkService]) maps the URI host to an
 * [AppAction] and routes to the matching flow. These are pure launcher buttons,
 * so there is no background callback and no widget data to snapshot — onUpdate
 * just (re)binds the click intents.
 */
class QuickCaptureWidget : HomeWidgetProvider() {
    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
        widgetData: SharedPreferences,
    ) {
        for (widgetId in appWidgetIds) {
            val views = RemoteViews(context.packageName, R.layout.quick_capture_widget).apply {
                setOnClickPendingIntent(R.id.qc_task, launchIntent(context, "addTask"))
                setOnClickPendingIntent(R.id.qc_expense, launchIntent(context, "addExpense"))
                setOnClickPendingIntent(R.id.qc_note, launchIntent(context, "newNote"))
                setOnClickPendingIntent(R.id.qc_chat, launchIntent(context, "chat"))
            }
            appWidgetManager.updateAppWidget(widgetId, views)
        }
    }

    private fun launchIntent(context: Context, action: String) =
        HomeWidgetLaunchIntent.getActivity(
            context,
            MainActivity::class.java,
            Uri.parse("lazyclaw://$action"),
        )
}
