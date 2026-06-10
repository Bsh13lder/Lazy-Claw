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
 * A header (brand + "+ Add") over a fixed 3-row task list (accent bullet ·
 * title · due pill) and a "+N more" overflow footer. The task data is a
 * PLAINTEXT snapshot the Flutter side writes via `home_widget` on every task
 * change AND from the 30-min background sync (see
 * `lib/core/home_widget_tasks.dart` / `lib/sync/background_sync.dart`):
 *   * `task_count`            — number of rows to show (0..3).
 *   * `task_<i>_title`        — the i-th task title.
 *   * `task_<i>_due`          — a short due label (e.g. `5:00 PM`, `Today`).
 *   * `task_more`             — footer label (`+2 more`) or '' to hide.
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
            // A binding throw must never abort onUpdate: an aborted update
            // leaves the widget frozen on whatever the launcher last had —
            // typically the static initialLayout, i.e. a header with no data.
            try {
                val views = RemoteViews(context.packageName, R.layout.tasks_widget).apply {
                    bindRows(widgetData, count)
                    bindStamp(widgetData)
                    // Tapping the body opens the Tasks page; the Add button opens
                    // the add-task flow. Both via home_widget's launch intent.
                    setOnClickPendingIntent(R.id.tw_root, launchIntent(context, "tasks"))
                    setOnClickPendingIntent(R.id.tw_add, launchIntent(context, "addTask"))
                }
                appWidgetManager.updateAppWidget(widgetId, views)
            } catch (_: Throwable) {
                // Fall back to a bare-but-alive paint (empty state visible) so
                // the widget shows SOMETHING rather than a stale frame.
                try {
                    val fallback = RemoteViews(context.packageName, R.layout.tasks_widget)
                    fallback.setViewVisibility(R.id.tw_empty, View.VISIBLE)
                    appWidgetManager.updateAppWidget(widgetId, fallback)
                } catch (_: Throwable) {
                    // Out of options for this widget id; skip it.
                }
            }
        }
    }

    /** Show the "last painted" HH:mm stamp the Flutter side writes; hidden
     *  until the first snapshot lands so a fresh widget isn't lying. */
    private fun RemoteViews.bindStamp(data: SharedPreferences) {
        val stamp = (data.all[KEY_STAMP] as? String).orEmpty()
        if (stamp.isBlank()) {
            setViewVisibility(R.id.tw_stamp, View.GONE)
        } else {
            setTextViewText(R.id.tw_stamp, stamp)
            setViewVisibility(R.id.tw_stamp, View.VISIBLE)
        }
    }

    /**
     * Read `task_count`, clamped to the 0..[ROWS] the layout can show.
     * Reads via `all[key]` so a write under ANY storage type (Int, Long from a
     * platform-channel quirk, String from a legacy build) resolves without a
     * ClassCastException — `getInt` on a mistyped key throws, and a throw here
     * would abort onUpdate and freeze the widget on its empty state.
     */
    private fun readCount(data: SharedPreferences): Int {
        val raw = when (val v = try { data.all[KEY_COUNT] } catch (_: Throwable) { null }) {
            is Int -> v
            is Long -> v.toInt()
            is String -> v.toIntOrNull() ?: 0
            else -> 0
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
            setViewVisibility(R.id.tw_more, View.GONE)
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
                // Hide an empty due pill entirely so undated tasks don't show
                // a blank chip.
                setViewVisibility(dueId(i), if (due.isBlank()) View.GONE else View.VISIBLE)
            } else {
                setRowVisibility(i, View.GONE)
            }
        }
        // "+N more" overflow footer.
        val more = data.getString(KEY_MORE, "") ?: ""
        if (more.isBlank()) {
            setViewVisibility(R.id.tw_more, View.GONE)
        } else {
            setTextViewText(R.id.tw_more, more)
            setViewVisibility(R.id.tw_more, View.VISIBLE)
        }
    }

    /** Show/hide a whole row — every row now has a wrapping container id. */
    private fun RemoteViews.setRowVisibility(index: Int, visibility: Int) {
        when (index) {
            0 -> setViewVisibility(R.id.tw_row0, visibility)
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
        const val KEY_MORE = "task_more"
        const val KEY_STAMP = "task_stamp"
        const val ROWS = 3
    }
}
