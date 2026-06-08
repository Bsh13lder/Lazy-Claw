package com.lazyclaw.lazyclaw_mobile

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.net.Uri
import android.view.View
import android.widget.RemoteViews
import es.antonborri.home_widget.HomeWidgetLaunchIntent
import es.antonborri.home_widget.HomeWidgetProvider

/**
 * LazyClaw "Today tasks" home-screen widget (~4x2).
 *
 * A header (brand + "+ Add") over a fixed 3-row task list. The task data is a
 * PLAINTEXT snapshot the Flutter side writes via `home_widget` on every task
 * change (see `lib/core/home_widget_tasks.dart`):
 *   * `task_count`            — number of rows to show (0..3).
 *   * `task_<i>_title`        — the i-th task title.
 *   * `task_<i>_due`          — a short due label (e.g. `5:00 PM`, `Today`).
 *
 * Click intents (RemoteViews-safe, both via `home_widget`'s launch intent):
 *   * whole body / header → `lazyclaw://tasks`   → opens the Tasks page.
 *   * "+ Add" button      → `lazyclaw://addTask`  → opens the add-task flow.
 *
 * Only RemoteViews-safe views are used (LinearLayout / TextView). When the
 * count is 0 we hide the rows and show the empty-state line.
 */
class TasksWidget : HomeWidgetProvider() {
    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
        widgetData: SharedPreferences,
    ) {
        val count = readCount(widgetData)
        for (widgetId in appWidgetIds) {
            val views = RemoteViews(context.packageName, R.layout.tasks_widget).apply {
                bindRows(widgetData, count)
                // Tapping the body opens the Tasks page; the Add button opens the
                // add-task flow. Both go through home_widget's launch intent.
                setOnClickPendingIntent(R.id.tw_root, launchIntent(context, "tasks"))
                setOnClickPendingIntent(R.id.tw_add, launchIntent(context, "addTask"))
            }
            appWidgetManager.updateAppWidget(widgetId, views)
        }
    }

    /** Read `task_count`, clamped to the 0..[ROWS] the layout can show. */
    private fun readCount(data: SharedPreferences): Int {
        val raw = try {
            data.getInt(KEY_COUNT, 0)
        } catch (_: Throwable) {
            // A legacy/odd write may have stored it as a String — tolerate it.
            data.getString(KEY_COUNT, null)?.toIntOrNull() ?: 0
        }
        return raw.coerceIn(0, ROWS)
    }

    /**
     * Fill the three fixed rows from the snapshot, or show the empty state.
     * Rows beyond [count] (or with a blank title) are hidden so a stale slot
     * can't render a leftover title.
     */
    private fun RemoteViews.bindRows(data: SharedPreferences, count: Int) {
        if (count <= 0) {
            setViewVisibility(R.id.tw_empty, View.VISIBLE)
            for (i in 0 until ROWS) setRowVisibility(i, View.GONE)
            return
        }
        setViewVisibility(R.id.tw_empty, View.GONE)
        for (i in 0 until ROWS) {
            val title = data.getString("task_${i}_title", "") ?: ""
            if (i < count && title.isNotBlank()) {
                val due = data.getString("task_${i}_due", "") ?: ""
                setRowVisibility(i, View.VISIBLE)
                setTextViewText(titleId(i), title)
                setTextViewText(dueId(i), due)
            } else {
                setRowVisibility(i, View.GONE)
            }
        }
    }

    /** Show/hide a whole row. Row 0's title+due live directly under the header
     *  (no wrapping container id), so toggle its two TextViews; rows 1 & 2 have
     *  a container we can toggle in one shot. */
    private fun RemoteViews.setRowVisibility(index: Int, visibility: Int) {
        when (index) {
            0 -> {
                setViewVisibility(R.id.tw_row0_title, visibility)
                setViewVisibility(R.id.tw_row0_due, visibility)
            }
            1 -> setViewVisibility(R.id.tw_row1, visibility)
            2 -> setViewVisibility(R.id.tw_row2, visibility)
        }
    }

    private fun titleId(index: Int): Int = when (index) {
        0 -> R.id.tw_row0_title
        1 -> R.id.tw_row1_title
        else -> R.id.tw_row2_title
    }

    private fun dueId(index: Int): Int = when (index) {
        0 -> R.id.tw_row0_due
        1 -> R.id.tw_row1_due
        else -> R.id.tw_row2_due
    }

    private fun launchIntent(context: Context, action: String) =
        HomeWidgetLaunchIntent.getActivity(
            context,
            MainActivity::class.java,
            Uri.parse("lazyclaw://$action"),
        )

    private companion object {
        const val KEY_COUNT = "task_count"
        const val ROWS = 3
    }
}
