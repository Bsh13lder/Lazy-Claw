import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/comment.dart';

void main() {
  test('parseComments round-trips the canonical shape', () {
    const raw =
        '[{"id":"c-1","ts":"2026-08-02T10:00:00+00:00","author":"agent",'
        '"text":"hi","subtask_id":"s-9"}]';
    final parsed = parseComments(raw);
    expect(parsed, hasLength(1));
    expect(parsed.first.author, 'agent');
    expect(parsed.first.subtaskId, 's-9');
    expect(serializeComments(parsed), raw);
  });

  test('parseComments tolerates garbage and empties', () {
    expect(parseComments(null), isEmpty);
    expect(parseComments(''), isEmpty);
    expect(parseComments('not json'), isEmpty);
    expect(parseComments('{"a":1}'), isEmpty);
    // entries with empty text are dropped; unknown author coerces to user
    expect(parseComments('[{"id":"c-1","text":""}]'), isEmpty);
    expect(parseComments('[{"text":"x","author":"martian"}]').first.author, 'user');
  });

  test('serializeComments returns null for empty (column clears)', () {
    expect(serializeComments(const []), isNull);
  });
}
