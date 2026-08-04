/// Debounced, coalesced auto-save scheduling for EDIT surfaces.
///
/// WHY this exists: every detail sheet in the app used to persist only when
/// Save was pressed. Change three fields, swipe the sheet away, lose all three
/// silently — the sheet gives no hint that the work was never written. This
/// controller is the scheduling half of the fix; the *deciding* half stays in
/// each sheet, because only the sheet knows its own three-way patch contract.
///
/// The split matters. This class deliberately knows NOTHING about dirtiness,
/// validity, or what a write contains. It only answers "when may a commit
/// run, and may two run at once". The sheet's [AutosaveCommit] callback is
/// simultaneously the dirty gate (returns [AutosaveOutcome.unchanged] when its
/// state matches the snapshot it last persisted), the validity gate (returns
/// [AutosaveOutcome.blocked] and shows an inline error rather than writing a
/// blank title over a good one), and the writer. That keeps the dangerous
/// judgement — "is this a real change, and is it safe to persist?" — next to
/// the patch rules it depends on, instead of duplicated in a generic helper
/// that cannot see them.
///
/// Guarantees:
///   * TEXT is debounced ([debounce]); DISCRETE controls (chips, pickers,
///     dropdowns, toggles) commit immediately via [markDirtyNow] — there is
///     nothing to debounce about a single tap.
///   * [flush] commits anything still pending. Sheets call it on dismiss, and
///     the optional lifecycle hook calls it when the app is backgrounded.
///   * Saves NEVER race: while one commit is in flight, later edits set a
///     single "run once more when you're done" flag, so N edits during a save
///     produce exactly ONE follow-up that sees the FINAL state.
library;

import 'dart:async';
import 'dart:convert';

import 'package:flutter/widgets.dart';

/// How long after the last keystroke a text edit is committed.
///
/// 700ms sits inside the 600–800ms band that reads as "it saved while I was
/// thinking" rather than either "it fought me mid-word" (too short — and every
/// half-typed number would be validated) or "did it save?" (too long).
const Duration kAutosaveDebounce = Duration(milliseconds: 700);

/// What a commit actually did. The sheet returns this; the controller only
/// uses it to pick a status.
enum AutosaveOutcome {
  /// A real write went out.
  written,

  /// Nothing differed from the last persisted snapshot — no write, no outbox
  /// row, no `updated_at` bump. Opening a sheet and closing it must land here.
  unchanged,

  /// The current state is not safe to persist (empty title, junk amount). The
  /// edit is HELD, not discarded, and the sheet shows why inline.
  blocked,
}

/// What the quiet on-sheet indicator renders.
enum AutosaveStatus {
  /// Nothing has been edited or written yet.
  idle,

  /// An edit is waiting on the debounce (or on an in-flight save).
  pending,

  /// A write is in flight.
  saving,

  /// Everything typed so far is persisted.
  saved,

  /// A write is being withheld because the state is invalid.
  blocked,

  /// A write was attempted and threw. Distinct from [blocked]: the user did
  /// nothing wrong, so telling them to fix a field would be a lie.
  failed,
}

typedef AutosaveCommit = Future<AutosaveOutcome> Function();

/// A canonical, comparable fingerprint of a sheet's editable state.
///
/// Sheets snapshot this on open and again after every successful write, then
/// compare before writing: equal ⇒ [AutosaveOutcome.unchanged]. JSON is used
/// rather than a hash because collisions here are silent data loss (a dropped
/// write), and because the encoding is stable across runs and readable in a
/// failing test's diff.
String autosaveSignature(List<Object?> parts) => jsonEncode(parts);

class AutosaveController extends ChangeNotifier {
  AutosaveController({
    required AutosaveCommit onCommit,
    this.debounce = kAutosaveDebounce,
    bool flushOnBackground = true,
  }) : _commit = onCommit {
    if (flushOnBackground) {
      // Backgrounding is the other way work disappears: the OS can kill the
      // process while a debounce is still ticking. `inactive` is the earliest
      // signal on iOS, `pause` the reliable one on Android — both are cheap
      // because a flush with nothing to write is a string comparison.
      _lifecycle = AppLifecycleListener(
        onInactive: _flushQuietly,
        onPause: _flushQuietly,
      );
    }
  }

  final AutosaveCommit _commit;
  final Duration debounce;

  AppLifecycleListener? _lifecycle;
  Timer? _timer;
  Future<void>? _current;
  bool _running = false;
  bool _queued = false;
  bool _wroteOnce = false;
  bool _disposed = false;

  /// Bound text controllers and the listener installed on each, so [dispose]
  /// can detach cleanly — a listener outliving this object would call
  /// [markDirty] on a dead controller.
  final Map<TextEditingController, VoidCallback> _bound = {};

  AutosaveStatus _status = AutosaveStatus.idle;
  AutosaveStatus get status => _status;

  /// True when there is no edit waiting and none in flight — the only moment
  /// it is safe for a caller to re-baseline without swallowing a real edit.
  bool get isIdle =>
      _timer == null &&
      !_running &&
      (_status == AutosaveStatus.idle || _status == AutosaveStatus.saved);

  /// Schedule a debounced commit from a TEXT field.
  void markDirty() {
    if (_disposed) return;
    _timer?.cancel();
    _timer = Timer(debounce, () {
      _timer = null;
      unawaited(_run());
    });
    _set(AutosaveStatus.pending);
  }

  /// Commit NOW — for discrete controls, where the user has already made a
  /// complete decision and waiting would only risk losing it.
  void markDirtyNow() {
    if (_disposed) return;
    _timer?.cancel();
    _timer = null;
    _set(AutosaveStatus.pending);
    unawaited(_run());
  }

  /// Commit anything pending and settle. Resolves only once the in-flight save
  /// AND any follow-up it queued have finished, so a caller may safely pop the
  /// route afterwards.
  Future<void> flush() {
    if (_disposed) return Future<void>.value();
    _timer?.cancel();
    _timer = null;
    return _run();
  }

  /// Drop a pending debounce without committing it. The one legitimate use is
  /// a DESTRUCTIVE action: after "delete this task", a queued field write
  /// would resurrect columns on a row that is on its way out.
  void cancelPending() {
    _timer?.cancel();
    _timer = null;
    _queued = false;
  }

  /// Mark dirty whenever [controller]'s TEXT changes.
  ///
  /// Text controllers also notify on selection/composing changes; those are
  /// filtered out here rather than left to the commit's signature check, so a
  /// caret move doesn't spin up a pointless debounce cycle on every tap.
  void bindText(TextEditingController controller) {
    if (_disposed || _bound.containsKey(controller)) return;
    var last = controller.text;
    void listener() {
      if (controller.text == last) return;
      last = controller.text;
      markDirty();
    }

    _bound[controller] = listener;
    controller.addListener(listener);
  }

  void _flushQuietly() => unawaited(flush());

  Future<void> _run() {
    // Already saving: register the intent and hand back the SAME future, which
    // only completes once the loop below has also run the follow-up. This is
    // the whole of the "never let two saves race" guarantee.
    if (_running) {
      _queued = true;
      return _current ?? Future<void>.value();
    }
    final loop = _loop();
    _current = loop;
    return loop;
  }

  Future<void> _loop() async {
    _running = true;
    try {
      do {
        _queued = false;
        _set(AutosaveStatus.saving);
        AutosaveOutcome outcome;
        try {
          outcome = await _commit();
        } catch (e) {
          // A throwing writer must not wedge the controller: report it, stop
          // looping (an immediate retry would just throw again), and let the
          // next edit try afresh.
          _queued = false;
          _set(AutosaveStatus.failed);
          continue;
        }
        switch (outcome) {
          case AutosaveOutcome.written:
            _wroteOnce = true;
            _set(AutosaveStatus.saved);
          case AutosaveOutcome.unchanged:
            // "Saved" only once something actually has been; before the first
            // write there is nothing to reassure the user about.
            _set(_wroteOnce ? AutosaveStatus.saved : AutosaveStatus.idle);
          case AutosaveOutcome.blocked:
            _set(AutosaveStatus.blocked);
        }
      } while (_queued);
    } finally {
      _running = false;
      _current = null;
    }
  }

  void _set(AutosaveStatus next) {
    if (_disposed || _status == next) return;
    _status = next;
    notifyListeners();
  }

  @override
  void dispose() {
    // Set FIRST: a commit started by a dismiss-time flush resumes after this
    // object is gone, and its _set must become a no-op rather than assert.
    _disposed = true;
    _timer?.cancel();
    _timer = null;
    _lifecycle?.dispose();
    _lifecycle = null;
    for (final entry in _bound.entries) {
      entry.key.removeListener(entry.value);
    }
    _bound.clear();
    super.dispose();
  }
}
