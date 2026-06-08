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
}
