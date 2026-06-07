package com.lazyclaw.lazyclaw_mobile

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.drawable.Drawable
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
 * (re)binds the click intents and paints the button icons.
 *
 * The icons are pre-rendered to bitmaps here rather than left as `android:src`
 * VectorDrawables: a widget host runs in a DIFFERENT process and many launchers
 * — notably Xiaomi MIUI / HyperOS — fail to inflate a VectorDrawable referenced
 * from a RemoteViews layout, leaving the icon blank. Rasterizing in our own
 * process and shipping the host a finished bitmap renders reliably everywhere.
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
                // Paint icons as bitmaps (RemoteViews-safe across all launchers).
                bindIcon(context, R.id.qc_task_icon, R.drawable.ic_shortcut_task)
                bindIcon(context, R.id.qc_expense_icon, R.drawable.ic_shortcut_expense)
                bindIcon(context, R.id.qc_note_icon, R.drawable.ic_shortcut_note)
                bindIcon(context, R.id.qc_chat_icon, R.drawable.ic_shortcut_chat)
                // Wire the deep-link launch intents.
                setOnClickPendingIntent(R.id.qc_task, launchIntent(context, "addTask"))
                setOnClickPendingIntent(R.id.qc_expense, launchIntent(context, "addExpense"))
                setOnClickPendingIntent(R.id.qc_note, launchIntent(context, "newNote"))
                setOnClickPendingIntent(R.id.qc_chat, launchIntent(context, "chat"))
            }
            appWidgetManager.updateAppWidget(widgetId, views)
        }
    }

    /**
     * Render [drawableRes] to a tinted bitmap and set it on [viewId]. Best-effort:
     * on any failure we leave the layout's `android:src` fallback in place (no
     * worse than before), so a single bad device can never blank the whole widget.
     */
    private fun RemoteViews.bindIcon(context: Context, viewId: Int, drawableRes: Int) {
        val bitmap = renderIcon(context, drawableRes) ?: return
        setImageViewBitmap(viewId, bitmap)
    }

    private fun renderIcon(context: Context, drawableRes: Int): Bitmap? {
        return try {
            val drawable: Drawable = context.getDrawable(drawableRes)?.mutate() ?: return null
            // Force the brand accent (the vector also declares it, but mutate()
            // can drop the resource tint — set it explicitly so the icon never
            // renders as an untinted/black glyph on the dark widget surface).
            @Suppress("DEPRECATION")
            drawable.setTint(context.resources.getColor(R.color.lz_accent))
            val px = (24 * context.resources.displayMetrics.density).toInt().coerceAtLeast(1)
            val bitmap = Bitmap.createBitmap(px, px, Bitmap.Config.ARGB_8888)
            val canvas = Canvas(bitmap)
            drawable.setBounds(0, 0, px, px)
            drawable.draw(canvas)
            bitmap
        } catch (_: Throwable) {
            null
        }
    }

    private fun launchIntent(context: Context, action: String) =
        HomeWidgetLaunchIntent.getActivity(
            context,
            MainActivity::class.java,
            Uri.parse("lazyclaw://$action"),
        )
}
