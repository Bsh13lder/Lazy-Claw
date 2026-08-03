// D3 (pure half) — the tag list transforms and the compact chip's summary
// label. Every rule the old always-on TAGS field enforced inline
// (trim / clamp / de-dup / immutable list) now lives here, where it can be
// tested without a widget tree.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_tags_field.dart';

void main() {
  group('tagsWithAdded', () {
    test('appends a trimmed tag', () {
      expect(tagsWithAdded(const ['a'], '  work  '), ['a', 'work']);
    });

    test('ignores blank / whitespace-only input', () {
      expect(tagsWithAdded(const ['a'], ''), ['a']);
      expect(tagsWithAdded(const ['a'], '   '), ['a']);
    });

    test('de-dups exactly (a repeat is a no-op, not a second chip)', () {
      expect(tagsWithAdded(const ['work'], 'work'), ['work']);
      expect(tagsWithAdded(const ['work'], '  work '), ['work']);
    });

    test('clamps to the max length', () {
      final long = 'x' * (kMaxTaskTagLength + 10);
      final out = tagsWithAdded(const [], long);
      expect(out.single.length, kMaxTaskTagLength);
    });

    test('de-dup happens AFTER clamping, so two over-long tags collapse', () {
      final long = 'y' * (kMaxTaskTagLength + 5);
      final once = tagsWithAdded(const [], long);
      expect(tagsWithAdded(once, '${long}zzz'), hasLength(1));
    });

    test('never mutates the input list', () {
      const original = ['a'];
      final out = tagsWithAdded(original, 'b');
      expect(original, ['a']);
      expect(identical(out, original), isFalse);
    });
  });

  group('tagsWithRemoved', () {
    test('removes every occurrence of the tag', () {
      expect(tagsWithRemoved(const ['a', 'b', 'a'], 'a'), ['b']);
    });

    test('is a no-op for an unknown tag', () {
      expect(tagsWithRemoved(const ['a'], 'zz'), ['a']);
    });

    test('never mutates the input list', () {
      const original = ['a', 'b'];
      final out = tagsWithRemoved(original, 'a');
      expect(original, ['a', 'b']);
      expect(identical(out, original), isFalse);
    });
  });

  group('taskTagsChipLabel', () {
    test('empty reads as an invitation, not a broken chip', () {
      expect(taskTagsChipLabel(const []), kTaskTagsEmptyLabel);
    });

    test('a single tag shows its own name', () {
      expect(taskTagsChipLabel(const ['work']), 'work');
    });

    test('several show the first plus an overflow count', () {
      expect(taskTagsChipLabel(const ['work', 'home', 'urgent']), 'work +2');
    });
  });
}
