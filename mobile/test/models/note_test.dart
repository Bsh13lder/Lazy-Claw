import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/note.dart';

void main() {
  group('Note.fromJson', () {
    test('parses a complete well-typed response', () {
      final json = {
        'id': 'note-abc',
        'title': 'My Note',
        'content': '# Hello\nSome **markdown** content.',
        'tags': ['personal', 'work'],
        'importance': 3,
        'pinned': true,
        'trace_session_id': null,
        'title_key': 'my-note',
        'created_at': '2026-06-05T10:00:00Z',
        'updated_at': '2026-06-05T11:00:00Z',
      };
      final note = Note.fromJson(json);
      expect(note.id, 'note-abc');
      expect(note.title, 'My Note');
      expect(note.content, '# Hello\nSome **markdown** content.');
      expect(note.tags, ['personal', 'work']);
      expect(note.importance, 3);
      expect(note.pinned, isTrue);
      expect(note.traceSessionId, isNull);
      expect(note.titleKey, 'my-note');
      expect(note.createdAt, '2026-06-05T10:00:00Z');
      expect(note.updatedAt, '2026-06-05T11:00:00Z');
    });

    test('tolerates null title', () {
      final note = Note.fromJson({
        'id': 'n1',
        'title': null,
        'content': 'Some content',
        'tags': [],
        'importance': 0,
        'pinned': false,
        'created_at': '2026-06-05T00:00:00Z',
        'updated_at': '2026-06-05T00:00:00Z',
      });
      expect(note.id, 'n1');
      expect(note.title, isNull);
      expect(note.content, 'Some content');
      expect(note.tags, isEmpty);
      expect(note.pinned, isFalse);
    });

    test('tolerates missing optional fields', () {
      final note = Note.fromJson({
        'id': 'n2',
        'content': 'Minimal',
        'created_at': '2026-06-05T00:00:00Z',
        'updated_at': '2026-06-05T00:00:00Z',
      });
      expect(note.id, 'n2');
      expect(note.title, isNull);
      expect(note.tags, isEmpty);
      expect(note.importance, 0);
      expect(note.pinned, isFalse);
      expect(note.traceSessionId, isNull);
      expect(note.titleKey, isNull);
    });

    test('tolerates tags sent as null (falls back to empty list)', () {
      final note = Note.fromJson({
        'id': 'n3',
        'content': 'Tag test',
        'tags': null,
        'created_at': '2026-06-05T00:00:00Z',
        'updated_at': '2026-06-05T00:00:00Z',
      });
      expect(note.tags, isEmpty);
    });

    test('tolerates importance sent as string', () {
      final note = Note.fromJson({
        'id': 'n4',
        'content': 'Importance test',
        'importance': '5',
        'created_at': '2026-06-05T00:00:00Z',
        'updated_at': '2026-06-05T00:00:00Z',
      });
      expect(note.importance, 5);
    });

    test('tolerates pinned sent as 0/1 integer', () {
      final note = Note.fromJson({
        'id': 'n5',
        'content': 'Pin test',
        'pinned': 1,
        'created_at': '2026-06-05T00:00:00Z',
        'updated_at': '2026-06-05T00:00:00Z',
      });
      expect(note.pinned, isTrue);
    });

    test('parses all null optional fields without error', () {
      final note = Note.fromJson({
        'id': 'n6',
        'title': null,
        'content': null,
        'tags': null,
        'importance': null,
        'pinned': null,
        'trace_session_id': null,
        'title_key': null,
        'created_at': null,
        'updated_at': null,
      });
      expect(note.id, 'n6');
      expect(note.content, '');
      expect(note.importance, 0);
      expect(note.pinned, isFalse);
    });

    test('copyWith creates a new immutable instance with updated fields', () {
      final original = Note.fromJson({
        'id': 'orig',
        'title': 'Original',
        'content': 'Content',
        'tags': ['a'],
        'importance': 1,
        'pinned': false,
        'created_at': '2026-06-05T00:00:00Z',
        'updated_at': '2026-06-05T00:00:00Z',
      });
      final updated = original.copyWith(title: 'Updated', pinned: true);
      expect(updated.title, 'Updated');
      expect(updated.pinned, isTrue);
      expect(updated.content, 'Content');
      expect(original.title, 'Original');
      expect(original.pinned, isFalse);
    });

    test('equality is id-based', () {
      final a = Note.fromJson({'id': 'same', 'content': 'A', 'created_at': '', 'updated_at': ''});
      final b = Note.fromJson({'id': 'same', 'content': 'B', 'created_at': '', 'updated_at': ''});
      expect(a, equals(b));
    });

    test('toJson round-trips title and content', () {
      final note = Note.fromJson({
        'id': 'rt1',
        'title': 'Round trip',
        'content': 'Check',
        'tags': ['t1'],
        'importance': 2,
        'pinned': true,
        'created_at': '2026-06-05T00:00:00Z',
        'updated_at': '2026-06-05T01:00:00Z',
      });
      final json = note.toJson();
      expect(json['id'], 'rt1');
      expect(json['title'], 'Round trip');
      expect(json['content'], 'Check');
      expect(json['tags'], ['t1']);
      expect(json['importance'], 2);
      expect(json['pinned'], isTrue);
    });

    test('contentPreview returns at most 120 chars of content', () {
      final longContent = 'A' * 200;
      final note = Note.fromJson({
        'id': 'prev',
        'content': longContent,
        'created_at': '',
        'updated_at': '',
      });
      expect(note.contentPreview.length, lessThanOrEqualTo(123));
    });

    test('contentPreview strips leading markdown heading line when title present', () {
      final note = Note.fromJson({
        'id': 'md',
        'title': 'My Note',
        'content': '# My Note\n\nSome body text here.',
        'created_at': '',
        'updated_at': '',
      });
      expect(note.contentPreview, isNot(startsWith('#')));
    });
  });
}
