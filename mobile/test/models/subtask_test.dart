// Pure (de)serialization tests for the Subtask model + the `steps`-column
// JSON shape `[{"id","title","done"}]`. No Flutter / no DB — just round-trips
// and tolerant-parsing edge cases.

import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/subtask.dart';

void main() {
  group('parseSubtasks', () {
    test('null / empty / whitespace → empty list', () {
      expect(parseSubtasks(null), isEmpty);
      expect(parseSubtasks(''), isEmpty);
      expect(parseSubtasks('   '), isEmpty);
    });

    test('malformed JSON → empty list (never throws)', () {
      expect(parseSubtasks('not json'), isEmpty);
      expect(parseSubtasks('{'), isEmpty);
    });

    test('a JSON object (not a list) → empty list', () {
      expect(parseSubtasks('{"id":"a","title":"x","done":false}'), isEmpty);
    });

    test('empty array → empty list', () {
      expect(parseSubtasks('[]'), isEmpty);
    });

    test('parses canonical {id,title,done} objects', () {
      final subs = parseSubtasks(
        '[{"id":"a","title":"First","done":true},'
        '{"id":"b","title":"Second","done":false}]',
      );
      expect(subs, hasLength(2));
      expect(subs[0], const Subtask(id: 'a', title: 'First', done: true));
      expect(subs[1], const Subtask(id: 'b', title: 'Second', done: false));
    });

    test('accepts bare-string entries as title-only steps (server loose shape)',
        () {
      final subs = parseSubtasks('["Buy milk","Call Sam"]');
      expect(subs, hasLength(2));
      expect(subs[0].title, 'Buy milk');
      expect(subs[0].done, isFalse);
      expect(subs[0].id, isNotEmpty); // a fresh id is minted
    });

    test('skips entries with an empty / whitespace title', () {
      final subs = parseSubtasks(
        '[{"id":"a","title":"  ","done":false},'
        '{"id":"b","title":"Keep","done":false},'
        '"   "]',
      );
      expect(subs, hasLength(1));
      expect(subs.single.title, 'Keep');
    });

    test('mints an id when one is missing', () {
      final subs = parseSubtasks('[{"title":"No id","done":false}]');
      expect(subs.single.id, isNotEmpty);
      expect(subs.single.title, 'No id');
    });

    test('coerces done from int / string forms', () {
      final subs = parseSubtasks(
        '[{"id":"a","title":"one","done":1},'
        '{"id":"b","title":"two","done":"true"},'
        '{"id":"c","title":"three","done":0},'
        '{"id":"d","title":"four","done":"false"}]',
      );
      expect(subs.map((s) => s.done).toList(), [true, true, false, false]);
    });

    test('trims the title', () {
      final subs = parseSubtasks('[{"id":"a","title":"  spaced  ","done":false}]');
      expect(subs.single.title, 'spaced');
    });
  });

  group('serializeSubtasks', () {
    test('empty list → null (clears the column)', () {
      expect(serializeSubtasks(const []), isNull);
    });

    test('emits the canonical {id,title,done} object array', () {
      final json = serializeSubtasks(const [
        Subtask(id: 'a', title: 'First', done: true),
        Subtask(id: 'b', title: 'Second', done: false),
      ]);
      final decoded = jsonDecode(json!) as List;
      expect(decoded, hasLength(2));
      expect(decoded.first, {'id': 'a', 'title': 'First', 'done': true});
      expect(decoded.last, {'id': 'b', 'title': 'Second', 'done': false});
    });
  });

  group('round-trip', () {
    test('parse(serialize(x)) preserves id / title / done', () {
      const original = [
        Subtask(id: 'x1', title: 'Alpha', done: false),
        Subtask(id: 'x2', title: 'Beta', done: true),
      ];
      final back = parseSubtasks(serializeSubtasks(original));
      expect(back, original);
    });

    test('an all-done list survives the round-trip', () {
      const original = [
        Subtask(id: 'z1', title: 'Done one', done: true),
        Subtask(id: 'z2', title: 'Done two', done: true),
      ];
      expect(parseSubtasks(serializeSubtasks(original)), original);
    });
  });

  group('progress helpers', () {
    test('subtaskProgress counts done / total', () {
      final p = subtaskProgress(const [
        Subtask(id: 'a', title: 'x', done: true),
        Subtask(id: 'b', title: 'y', done: false),
        Subtask(id: 'c', title: 'z', done: true),
      ]);
      expect(p.done, 2);
      expect(p.total, 3);
    });

    test('subtaskProgressLabel is null for an empty list', () {
      expect(subtaskProgressLabel(const []), isNull);
    });

    test('subtaskProgressLabel formats as done/total', () {
      expect(
        subtaskProgressLabel(const [
          Subtask(id: 'a', title: 'x', done: true),
          Subtask(id: 'b', title: 'y', done: false),
        ]),
        '1/2',
      );
    });
  });

  group('Subtask helpers', () {
    test('copyWith toggles done without touching id/title', () {
      const s = Subtask(id: 'a', title: 'Task', done: false);
      final toggled = s.copyWith(done: true);
      expect(toggled.done, isTrue);
      expect(toggled.id, 'a');
      expect(toggled.title, 'Task');
      expect(s.done, isFalse); // immutable — original unchanged
    });

    test('newSubtaskId mints distinct prefixed ids', () {
      final a = newSubtaskId();
      final b = newSubtaskId();
      expect(a, startsWith('s-'));
      expect(a, isNot(b));
    });
  });

  // ── Timestamps: created_at / completed_at ────────────────────────────────
  //
  // Both keys are OMITTED when null so an ordinary checklist keeps its lean
  // {id,title,done} shape — the same rule the server applies to its
  // `cascaded` marker. Parsing degrades to null rather than throwing, because
  // these blobs come off the wire and out of the offline cache where a stale
  // or hand-edited value must never take the whole checklist down.
  group('Subtask timestamps', () {
    const createdIso = '2026-08-04T09:15:00.000000+00:00';
    const completedIso = '2026-08-04T11:42:13.500000+00:00';

    group('toJson lean shape', () {
      test('omits both keys when null', () {
        const s = Subtask(id: 'a', title: 'Plain', done: false);
        expect(s.toJson(), {'id': 'a', 'title': 'Plain', 'done': false});
        expect(s.toJson().containsKey('created_at'), isFalse);
        expect(s.toJson().containsKey('completed_at'), isFalse);
      });

      test('emits created_at only when completed_at is null', () {
        const s = Subtask(
          id: 'a',
          title: 'Open',
          done: false,
          createdAt: createdIso,
        );
        expect(s.toJson(), {
          'id': 'a',
          'title': 'Open',
          'done': false,
          'created_at': createdIso,
        });
      });

      test('emits both when both are set', () {
        const s = Subtask(
          id: 'a',
          title: 'Ticked',
          done: true,
          createdAt: createdIso,
          completedAt: completedIso,
        );
        expect(s.toJson(), {
          'id': 'a',
          'title': 'Ticked',
          'done': true,
          'created_at': createdIso,
          'completed_at': completedIso,
        });
      });
    });

    group('fromMap tolerance', () {
      test('missing keys → null (never throws)', () {
        final s = Subtask.fromMap({'id': 'a', 'title': 'x', 'done': false})!;
        expect(s.createdAt, isNull);
        expect(s.completedAt, isNull);
      });

      test('explicit JSON null → null', () {
        final s = Subtask.fromMap({
          'id': 'a',
          'title': 'x',
          'done': false,
          'created_at': null,
          'completed_at': null,
        })!;
        expect(s.createdAt, isNull);
        expect(s.completedAt, isNull);
      });

      test('non-string values → null', () {
        final s = Subtask.fromMap({
          'id': 'a',
          'title': 'x',
          'done': false,
          'created_at': 1754300000,
          'completed_at': {'nested': 'map'},
        })!;
        expect(s.createdAt, isNull);
        expect(s.completedAt, isNull);
      });

      test('unparseable garbage strings → null', () {
        final s = Subtask.fromMap({
          'id': 'a',
          'title': 'x',
          'done': false,
          'created_at': 'not-a-date',
          'completed_at': '   ',
        })!;
        expect(s.createdAt, isNull);
        expect(s.completedAt, isNull);
      });

      test('a valid timestamp is kept VERBATIM, not reformatted', () {
        // Re-emitting a parsed DateTime would rewrite the server's `+00:00`
        // into Dart's `Z` and silently churn every row on the next sync.
        final s = Subtask.fromMap({
          'id': 'a',
          'title': 'x',
          'done': true,
          'created_at': createdIso,
          'completed_at': completedIso,
        })!;
        expect(s.createdAt, createdIso);
        expect(s.completedAt, completedIso);
      });

      test('accepts the client-minted `Z` shape too', () {
        final s = Subtask.fromMap({
          'id': 'a',
          'title': 'x',
          'done': true,
          'created_at': '2026-08-04T09:15:00.000Z',
        })!;
        expect(s.createdAt, '2026-08-04T09:15:00.000Z');
      });
    });

    group('copyWith', () {
      test('carries timestamps through an unrelated edit', () {
        const s = Subtask(
          id: 'a',
          title: 'Old',
          done: true,
          createdAt: createdIso,
          completedAt: completedIso,
        );
        final renamed = s.copyWith(title: 'New');
        expect(renamed.title, 'New');
        expect(renamed.createdAt, createdIso);
        expect(renamed.completedAt, completedIso);
      });

      test('sets completedAt', () {
        const s = Subtask(
          id: 'a',
          title: 'x',
          done: false,
          createdAt: createdIso,
        );
        final ticked = s.copyWith(done: true, completedAt: completedIso);
        expect(ticked.done, isTrue);
        expect(ticked.completedAt, completedIso);
        expect(s.completedAt, isNull); // original untouched
      });

      test('CLEARS completedAt via clearCompletedAt — the case `?? this.x` '
          'silently cannot express', () {
        const s = Subtask(
          id: 'a',
          title: 'x',
          done: true,
          createdAt: createdIso,
          completedAt: completedIso,
        );

        // The naive idiom: passing null means "unchanged", so this does NOT
        // clear. Asserted so a regression to `?? this.x` fails loudly here.
        expect(s.copyWith(done: false).completedAt, completedIso);

        final unticked = s.copyWith(done: false, clearCompletedAt: true);
        expect(unticked.done, isFalse);
        expect(unticked.completedAt, isNull);
        expect(unticked.createdAt, createdIso); // creation time survives
      });
    });

    group('== / hashCode', () {
      test('two sub-tasks differing ONLY by completedAt are NOT equal', () {
        const a = Subtask(id: 'a', title: 'x', done: true);
        const b = Subtask(
          id: 'a',
          title: 'x',
          done: true,
          completedAt: completedIso,
        );
        expect(a, isNot(equals(b)));
        expect(a.hashCode, isNot(b.hashCode));
      });

      test('two sub-tasks differing ONLY by createdAt are NOT equal', () {
        const a = Subtask(id: 'a', title: 'x', done: false);
        const b = Subtask(
          id: 'a',
          title: 'x',
          done: false,
          createdAt: createdIso,
        );
        expect(a, isNot(equals(b)));
        expect(a.hashCode, isNot(b.hashCode));
      });

      test('identical timestamps compare equal', () {
        const a = Subtask(
          id: 'a',
          title: 'x',
          done: true,
          createdAt: createdIso,
          completedAt: completedIso,
        );
        const b = Subtask(
          id: 'a',
          title: 'x',
          done: true,
          createdAt: createdIso,
          completedAt: completedIso,
        );
        expect(a, equals(b));
        expect(a.hashCode, b.hashCode);
      });
    });

    group('round-trip', () {
      test('both timestamps survive parse(serialize(x))', () {
        const original = [
          Subtask(
            id: 'x1',
            title: 'Alpha',
            done: true,
            createdAt: createdIso,
            completedAt: completedIso,
          ),
          Subtask(
            id: 'x2',
            title: 'Beta',
            done: false,
            createdAt: createdIso,
          ),
        ];
        expect(parseSubtasks(serializeSubtasks(original)), original);
      });

      test('a LEGACY sub-task with no timestamps survives parse→serialize '
          'byte-identically (old rows are never backfilled)', () {
        const legacyJson =
            '[{"id":"old1","title":"Legacy","done":false},'
            '{"id":"old2","title":"Legacy done","done":true}]';
        final parsed = parseSubtasks(legacyJson);
        expect(parsed.every((s) => s.createdAt == null), isTrue);
        expect(parsed.every((s) => s.completedAt == null), isTrue);
        expect(serializeSubtasks(parsed), legacyJson);
      });

      test('a bare-string entry gets NO minted timestamp (no backfill)', () {
        final subs = parseSubtasks('["Buy milk"]');
        expect(subs.single.createdAt, isNull);
        expect(subs.single.completedAt, isNull);
      });

      test('a mixed legacy/new list keeps each row lean or full as stored', () {
        final subs = parseSubtasks(
          '[{"id":"old","title":"Legacy","done":false},'
          '{"id":"new","title":"Fresh","done":false,'
          '"created_at":"$createdIso"}]',
        );
        expect(subs[0].toJson().containsKey('created_at'), isFalse);
        expect(subs[1].toJson()['created_at'], createdIso);
      });
    });

    group('subtaskNowIso', () {
      test('mints a parseable UTC ISO-8601 instant', () {
        final iso = subtaskNowIso();
        final parsed = DateTime.tryParse(iso);
        expect(parsed, isNotNull);
        expect(parsed!.isUtc, isTrue);
      });

      test('survives fromMap validation (what the editor stamps must parse)',
          () {
        final s = Subtask.fromMap({
          'id': 'a',
          'title': 'x',
          'done': false,
          'created_at': subtaskNowIso(),
        })!;
        expect(s.createdAt, isNotNull);
      });
    });
  });
}
