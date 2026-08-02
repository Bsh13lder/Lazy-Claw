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
/// section name. Not yet consumed by a screen — reserved for the List-view
/// section-collapse feature.
String kPrefListSectionCollapsed(String section) =>
    'tasks.list.$section.collapsed';

// ── Provider ─────────────────────────────────────────────────────────────────

/// Local UI-prefs store backed by the encrypted DB's `ui_prefs` table. Same
/// wiring as [taskDaoProvider] in `tasks_provider.dart`.
final uiPrefsDaoProvider = Provider<UiPrefsDao>((ref) {
  return UiPrefsDao(ref.watch(appDatabaseProvider));
});
