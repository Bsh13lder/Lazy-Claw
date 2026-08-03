import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../local/ui_prefs_dao.dart';
import 'tasks_provider.dart' show appDatabaseProvider;

// ── Persisted UI-state keys ─────────────────────────────────────────────────
//
// Client-local only (see UiPrefsDao) — never synced, never encrypted, never
// leaves the device. Centralized here so every screen that reads/writes a
// given piece of UI state agrees on its key.

/// The Tasks → Projects view's expanded-bucket names (a JSON string set).
const String kPrefProjectsExpanded = 'tasks.projects.expanded';

/// The Tasks → Projects view's "hide completed" toggle (a bool).
const String kPrefProjectsHideCompleted = 'tasks.projects.hideCompleted';

/// The Tasks → List view's per-section collapsed flag (a bool), keyed by
/// section name. Consumed by `TaskSection` in tasks_screen.dart (loaded via
/// `TasksScreen._loadSectionCollapsedPrefs`, persisted via
/// `TasksScreen._onSectionCollapsedChanged`).
String kPrefListSectionCollapsed(String section) =>
    'tasks.list.$section.collapsed';

/// The Tasks → Calendar view's "Show repeats" toggle (a bool) — whether
/// recurring tasks' projected ghost occurrences render at all. Default-ON
/// (true) preserves the pre-toggle behavior; OFF skips
/// `expandRecurringForRange` entirely rather than just hiding its output
/// (see `TaskCalendarView.showRepeats`). Added for the 2026-08 "every day
/// says ○ ○ ○ +37" regression fix — even a single ghost marker per day may
/// be unwanted for a user with dozens of recurring tasks.
const String kPrefCalendarShowRepeats = 'tasks.calendar.showRepeats';

// ── Provider ─────────────────────────────────────────────────────────────────

/// Local UI-prefs store backed by the encrypted DB's `ui_prefs` table. Same
/// wiring as [taskDaoProvider] in `tasks_provider.dart`.
final uiPrefsDaoProvider = Provider<UiPrefsDao>((ref) {
  return UiPrefsDao(ref.watch(appDatabaseProvider));
});
