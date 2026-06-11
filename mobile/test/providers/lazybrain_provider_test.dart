import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/providers/lazybrain_provider.dart';
import 'package:lazyclaw_mobile/repositories/lazybrain_repository.dart';

// ── Canned payloads (real backend shapes — see lazybrain_repository_test) ────

Map<String, dynamic> _noteJson(String id, {List<String> tags = const []}) => {
      'id': id,
      'title': 'Note $id',
      'content': 'body of $id',
      'tags': tags,
      'importance': 5,
      'pinned': false,
      'trace_session_id': null,
      'title_key': 'note $id',
      'memory_type': 'user',
      'created_at': '2026-06-10 08:00:00.000000',
      'updated_at': '2026-06-10 08:00:00.000000',
    };

/// Routes GETs by path/queryParams so one transport can serve the three
/// concurrent fetches `load()` fires (journal + tags + pinned).
class _RoutingTransport implements LazyBrainTransport {
  bool failJournal = false;
  bool failTagFetch = false;
  Map<String, dynamic>? lastTagQuery;

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async {
    if (path == '/api/lazybrain/journal') {
      if (failJournal) throw ApiError(500, 'journal down');
      return {
        'notes': [_noteJson('j1', tags: ['journal'])],
      };
    }
    if (path == '/api/lazybrain/tags') {
      return {
        'tags': [
          {'tag': 'journal', 'count': 3},
          {'tag': 'topic/browser', 'count': 2},
        ],
      };
    }
    if (path == '/api/lazybrain/notes') {
      if (queryParams?['pinned'] == 'true') {
        return {
          'notes': [_noteJson('p1')],
        };
      }
      if (queryParams?['tag'] != null) {
        if (failTagFetch) throw ApiError(502, 'tag fetch failed');
        lastTagQuery = queryParams;
        return {
          'notes': [
            _noteJson('t1', tags: [queryParams!['tag'] as String]),
          ],
        };
      }
    }
    throw StateError('unexpected GET $path');
  }

  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    throw StateError('unexpected POST $path');
  }
}

void main() {
  test('load() populates journal, tags and pinned', () async {
    final notifier =
        BrainNotifier(LazyBrainRepository(_RoutingTransport()));
    await notifier.load();
    final state = notifier.state;
    expect(state.isLoading, isFalse);
    expect(state.error, isNull);
    expect(state.journal.single.id, 'j1');
    expect(state.tags.length, 2);
    expect(state.pinned.single.id, 'p1');
  });

  test('load() failure surfaces the error without throwing', () async {
    final transport = _RoutingTransport()..failJournal = true;
    final notifier = BrainNotifier(LazyBrainRepository(transport));
    await notifier.load();
    expect(notifier.state.isLoading, isFalse);
    expect(notifier.state.error, contains('journal down'));
  });

  test('selectTag loads notes for the tag; clearTag resets', () async {
    final transport = _RoutingTransport();
    final notifier = BrainNotifier(LazyBrainRepository(transport));
    await notifier.selectTag('topic/browser');
    expect(notifier.state.activeTag, 'topic/browser');
    expect(notifier.state.tagNotes.single.id, 't1');
    expect(transport.lastTagQuery, {'tag': 'topic/browser'});

    notifier.clearTag();
    expect(notifier.state.activeTag, isNull);
    expect(notifier.state.tagNotes, isEmpty);
  });

  test('selectTag failure stores tagError and keeps sections intact',
      () async {
    final transport = _RoutingTransport();
    final notifier = BrainNotifier(LazyBrainRepository(transport));
    await notifier.load();
    transport.failTagFetch = true;
    await notifier.selectTag('topic/browser');
    expect(notifier.state.tagError, contains('tag fetch failed'));
    expect(notifier.state.journal, isNotEmpty);
    expect(notifier.state.tags, isNotEmpty);
  });
}
