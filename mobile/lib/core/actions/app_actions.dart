import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Home-screen quick-capture actions that can be triggered from OUTSIDE the
/// running app:
///
///  * launcher long-press shortcuts (`quick_actions`), and
///  * the home-screen Quick-Capture widget (`home_widget`).
///
/// Each action deep-links into the app to a specific destination + flow. The
/// mapping helpers below are intentionally PURE (no Flutter bindings, no
/// provider access) so the shortcut/URI → action → route resolution can be
/// unit-tested in isolation.
enum AppAction { addTask, addExpense, chat, newNote }

/// Stable string identifiers for each action.
///
/// These are shared with the native side: they are the launcher shortcut
/// `type` values (`quick_actions`) AND must canonically match the
/// `lazyclaw://<host>` deep-link URIs fired by the home-screen widget. The
/// matcher is underscore/case-insensitive, so the launcher's snake_case
/// (`add_task`) and the widget URI's camelCase host (`addTask`) both resolve
/// to the same [AppAction].
const Map<AppAction, String> kActionIds = {
  AppAction.addTask: 'add_task',
  AppAction.addExpense: 'add_expense',
  AppAction.chat: 'chat',
  AppAction.newNote: 'new_note',
};

/// The deep-link URI scheme the home-screen widget launches the app with
/// (e.g. `lazyclaw://addTask`).
const String kDeepLinkScheme = 'lazyclaw';

/// Canonicalize an identifier so the launcher's `add_task` and the widget URI's
/// `addTask` collapse to the same key.
String _canon(String raw) => raw.toLowerCase().replaceAll('_', '');

AppAction? _matchCanon(String? raw) {
  if (raw == null || raw.isEmpty) return null;
  final target = _canon(raw);
  for (final action in AppAction.values) {
    if (_canon(kActionIds[action]!) == target) return action;
  }
  return null;
}

/// Map a launcher shortcut `type` (e.g. `add_task`) → [AppAction], or null when
/// it doesn't correspond to a known action. Pure + testable.
AppAction? appActionForShortcut(String? type) => _matchCanon(type);

/// Map a home-screen widget launch [uri] (e.g. `lazyclaw://addTask`) →
/// [AppAction], or null. The action lives in the URI host, falling back to the
/// first path segment for robustness. Pure + testable.
AppAction? appActionForUri(Uri? uri) {
  if (uri == null) return null;
  final raw = uri.host.isNotEmpty
      ? uri.host
      : (uri.pathSegments.isNotEmpty ? uri.pathSegments.first : null);
  return _matchCanon(raw);
}

/// The go_router branch each action navigates to. `addTask` / `newNote` both
/// land on the Tasks tab (Notes is a segment INSIDE that tab); the screen then
/// opens the right sheet. Pure + testable.
String routeForAction(AppAction action) {
  switch (action) {
    case AppAction.addTask:
    case AppAction.newNote:
      return '/tasks';
    case AppAction.addExpense:
      return '/expenses';
    case AppAction.chat:
      return '/chat';
  }
}

/// Spec for a single launcher long-press shortcut. [icon] is the name of a
/// vector drawable under `android/app/src/main/res/drawable/` (no extension).
class ShortcutSpec {
  final String type;
  final String title;
  final String icon;
  const ShortcutSpec({
    required this.type,
    required this.title,
    required this.icon,
  });
}

/// The four launcher long-press shortcuts. Re-registered on every app start
/// because MIUI / HyperOS can silently clear dynamic shortcuts.
const List<ShortcutSpec> kShortcutSpecs = [
  ShortcutSpec(type: 'add_task', title: 'Add task', icon: 'ic_shortcut_task'),
  ShortcutSpec(
      type: 'add_expense', title: 'Add expense', icon: 'ic_shortcut_expense'),
  ShortcutSpec(type: 'chat', title: 'Chat', icon: 'ic_shortcut_chat'),
  ShortcutSpec(type: 'new_note', title: 'New note', icon: 'ic_shortcut_note'),
];

/// A one-shot pending deep-link action. Set by [appActionForShortcut] /
/// [appActionForUri] resolution when a shortcut or widget button fires, then
/// REPLAYED + consumed once the relevant screen is mounted. Decoupling the
/// trigger (which can fire before the router/screen exists on cold start) from
/// the replay handles the cold-start race cleanly.
final pendingActionProvider = StateProvider<AppAction?>((ref) => null);

/// Consume the pending action iff it is one of [mine]; clears the provider and
/// returns the consumed action, else returns null (leaving any foreign pending
/// action untouched for its owning screen to handle).
AppAction? takePendingAction(WidgetRef ref, Set<AppAction> mine) {
  final pending = ref.read(pendingActionProvider);
  if (pending != null && mine.contains(pending)) {
    ref.read(pendingActionProvider.notifier).state = null;
    return pending;
  }
  return null;
}

/// Drains the pending deep-link action for a screen the moment it is visible —
/// regardless of which frame the screen happens to mount on.
///
/// COLD-START PROBLEM this solves: a `+ Task` / `+ Expense` / `+ Note` widget
/// button (or launcher shortcut) sets [pendingActionProvider] *before* the
/// destination screen exists. The destination is then built as a *result* of
/// the navigation listener, so:
///   * a `build`-time `ref.listen` never fires (it only catches CHANGES, and
///     the value was already set before the screen subscribed), and
///   * a one-shot `initState` post-frame can mis-fire if auth-redirect churn
///     (`unauthenticated → loading → authenticated`) rebuilds the shell or
///     bounces the location, stranding the action.
///
/// [drainPendingAction] is **idempotent** and meant to be called from EVERY
/// `build` of the owning screen: it schedules a single post-frame consume iff a
/// matching action is currently pending and one isn't already in flight. Because
/// it re-arms on each build, the screen drains its action no matter which frame
/// it becomes visible on — cold start, warm tap, or a rebuild mid-redirect.
///
/// [onDrained] is invoked (post-frame, guarded by [isMounted]) with the consumed
/// action so the screen can open its sheet / switch its segment.
void drainPendingAction(
  WidgetRef ref, {
  required Set<AppAction> mine,
  required bool Function() isMounted,
  required void Function(AppAction action) onDrained,
}) {
  // Peek without consuming — only schedule work when one of OUR actions is up.
  final pending = ref.read(pendingActionProvider);
  if (pending == null || !mine.contains(pending)) return;
  WidgetsBinding.instance.addPostFrameCallback((_) {
    if (!isMounted()) return;
    // Re-check + consume atomically at drain time: another consumer (or a prior
    // post-frame from this same screen) may have already taken it.
    final taken = takePendingAction(ref, mine);
    if (taken != null) onDrained(taken);
  });
}
