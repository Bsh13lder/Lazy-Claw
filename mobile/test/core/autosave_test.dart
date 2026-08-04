// Unit tests for the debounce / coalesce / flush machinery behind auto-save.
//
// Plain `test()` (not `testWidgets`) on purpose: this is a timer + Future state
// machine with no widget tree, so real timers with a tiny debounce are both
// faster and less surprising than FakeAsync. The binding is still initialised
// because [AutosaveController] installs an [AppLifecycleListener].

import 'dart:async';

import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/autosave.dart';

/// A hand-rolled recorder so the outcome of each commit — and, for the
/// coalescing tests, WHEN it resolves — is fully under the test's control.
class _Recorder {
  _Recorder({this.outcome = AutosaveOutcome.written});

  AutosaveOutcome outcome;
  int calls = 0;

  /// When non-null, every commit parks on this until the test completes it.
  Completer<void>? gate;

  /// Snapshot of a caller-supplied value at each commit, so a test can prove
  /// the SECOND (coalesced) commit saw the final state.
  final List<String> seen = [];
  String Function() read = () => '';

  Future<AutosaveOutcome> commit() async {
    calls++;
    seen.add(read());
    final g = gate;
    if (g != null) await g.future;
    if (outcome == AutosaveOutcome.blocked) throw _Blocked();
    return outcome;
  }

  Future<AutosaveOutcome> commitBlocked() async {
    calls++;
    return AutosaveOutcome.blocked;
  }
}

class _Blocked implements Exception {}

const _debounce = Duration(milliseconds: 30);

/// Comfortably past [_debounce] without making the suite slow.
Future<void> _settle() => Future<void>.delayed(const Duration(milliseconds: 90));

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  AutosaveController make(
    Future<AutosaveOutcome> Function() commit, {
    Duration debounce = _debounce,
  }) => AutosaveController(
    onCommit: commit,
    debounce: debounce,
    // The lifecycle hook needs a real engine to be meaningful; these tests
    // drive flush() directly.
    flushOnBackground: false,
  );

  group('debounce', () {
    test('collapses a burst of markDirty into ONE commit', () async {
      final rec = _Recorder();
      final c = make(rec.commit);
      addTearDown(c.dispose);

      c.markDirty();
      c.markDirty();
      c.markDirty();
      expect(rec.calls, 0, reason: 'nothing may run before the debounce lapses');

      await _settle();
      expect(rec.calls, 1);
    });

    test('markDirtyNow commits without waiting for the debounce', () async {
      final rec = _Recorder();
      final c = make(rec.commit, debounce: const Duration(seconds: 10));
      addTearDown(c.dispose);

      c.markDirtyNow();
      // One microtask turn is enough — no timer is involved.
      await Future<void>.delayed(Duration.zero);
      expect(rec.calls, 1);
    });

    test('markDirtyNow cancels a pending debounce rather than stacking on it',
        () async {
      final rec = _Recorder();
      final c = make(rec.commit);
      addTearDown(c.dispose);

      c.markDirty();
      c.markDirtyNow();
      await _settle();
      expect(rec.calls, 1);
    });
  });

  group('flush', () {
    test('commits a pending debounced edit instead of dropping it', () async {
      final rec = _Recorder();
      final c = make(rec.commit, debounce: const Duration(seconds: 10));
      addTearDown(c.dispose);

      c.markDirty();
      await c.flush();
      expect(rec.calls, 1);
    });

    test('still calls the commit when nothing is pending — the commit itself '
        'is the dirty gate, and it answers "unchanged"', () async {
      final rec = _Recorder(outcome: AutosaveOutcome.unchanged);
      final c = make(rec.commit);
      addTearDown(c.dispose);

      await c.flush();
      expect(rec.calls, 1);
      expect(c.status, AutosaveStatus.idle,
          reason: 'an unchanged commit must not read as "Saved"');
    });

  });

  group('coalescing', () {
    test('a change during an in-flight save queues exactly ONE follow-up',
        () async {
      final gate = Completer<void>();
      final rec = _Recorder()..gate = gate;
      var value = 'a';
      rec.read = () => value;
      final c = make(rec.commit);
      addTearDown(c.dispose);

      c.markDirtyNow();
      await Future<void>.delayed(Duration.zero);
      expect(rec.calls, 1);

      // Three more edits while the first save is parked.
      value = 'b';
      c.markDirtyNow();
      value = 'c';
      c.markDirtyNow();
      value = 'd';
      c.markDirtyNow();
      expect(rec.calls, 1, reason: 'no second save may start mid-flight');

      gate.complete();
      await _settle();

      expect(rec.calls, 2, reason: 'three queued edits collapse to one save');
      expect(rec.seen, ['a', 'd'],
          reason: 'the follow-up must see the FINAL state, not an interim one');
    });

    test('flush resolves only after the queued follow-up has run', () async {
      final gate = Completer<void>();
      final rec = _Recorder()..gate = gate;
      final c = make(rec.commit);
      addTearDown(c.dispose);

      c.markDirtyNow();
      await Future<void>.delayed(Duration.zero);
      c.markDirtyNow();

      var resolved = false;
      final pending = c.flush().then((_) => resolved = true);
      await Future<void>.delayed(Duration.zero);
      expect(resolved, isFalse);

      gate.complete();
      await pending;
      expect(resolved, isTrue);
      expect(rec.calls, 2);
    });
  });

  group('status', () {
    test('idle → pending → saving → saved', () async {
      final gate = Completer<void>();
      final rec = _Recorder()..gate = gate;
      final c = make(rec.commit);
      addTearDown(c.dispose);

      final seen = <AutosaveStatus>[c.status];
      c.addListener(() => seen.add(c.status));

      c.markDirty();
      await _settle();
      gate.complete();
      await _settle();

      expect(seen, [
        AutosaveStatus.idle,
        AutosaveStatus.pending,
        AutosaveStatus.saving,
        AutosaveStatus.saved,
      ]);
    });

    test('a blocked commit surfaces as blocked and does not become "Saved"',
        () async {
      final rec = _Recorder();
      final c = make(rec.commitBlocked);
      addTearDown(c.dispose);

      c.markDirtyNow();
      await _settle();
      expect(c.status, AutosaveStatus.blocked);
    });

    test('a THROWN commit is reported as failed, not swallowed, and leaves the '
        'controller usable', () async {
      var throwNext = true;
      var calls = 0;
      final c = make(() async {
        calls++;
        if (throwNext) throw StateError('write failed');
        return AutosaveOutcome.written;
      });
      addTearDown(c.dispose);

      c.markDirtyNow();
      await _settle();
      expect(c.status, AutosaveStatus.failed);

      throwNext = false;
      c.markDirtyNow();
      await _settle();
      expect(calls, 2, reason: 'a failure must not wedge the controller');
      expect(c.status, AutosaveStatus.saved);
    });

    test('an unchanged commit AFTER a real write still reads as "Saved"',
        () async {
      var outcome = AutosaveOutcome.written;
      final c = make(() async => outcome);
      addTearDown(c.dispose);

      c.markDirtyNow();
      await _settle();
      expect(c.status, AutosaveStatus.saved);

      outcome = AutosaveOutcome.unchanged;
      c.markDirtyNow();
      await _settle();
      expect(c.status, AutosaveStatus.saved);
    });
  });

  group('text binding', () {
    test('a text change marks dirty; a selection-only change does not',
        () async {
      final rec = _Recorder();
      final c = make(rec.commit);
      final field = TextEditingController(text: 'hello');
      addTearDown(field.dispose);
      addTearDown(c.dispose);

      c.bindText(field);

      // Selection-only: same text, new selection.
      field.selection = const TextSelection.collapsed(offset: 2);
      await _settle();
      expect(rec.calls, 0);

      field.text = 'hello there';
      await _settle();
      expect(rec.calls, 1);
    });

    test('dispose unbinds, so a later controller change schedules nothing',
        () async {
      final rec = _Recorder();
      final c = make(rec.commit);
      final field = TextEditingController(text: 'a');
      addTearDown(field.dispose);

      c.bindText(field);
      c.dispose();

      field.text = 'b';
      await _settle();
      expect(rec.calls, 0);
    });
  });

  test('dispose cancels a pending debounce', () async {
    final rec = _Recorder();
    final c = make(rec.commit);

    c.markDirty();
    c.dispose();

    await _settle();
    expect(rec.calls, 0);
  });

  test('autosaveSignature is order- and value-sensitive but stable', () {
    expect(autosaveSignature(['a', 1, null]), autosaveSignature(['a', 1, null]));
    expect(autosaveSignature(['a', 1]), isNot(autosaveSignature([1, 'a'])));
    expect(autosaveSignature([null]), isNot(autosaveSignature([''])));
  });
}
