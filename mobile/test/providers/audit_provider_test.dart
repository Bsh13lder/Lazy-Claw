import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/audit_provider.dart';
import 'package:lazyclaw_mobile/repositories/audit_repository.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

class _FakeTransport implements AuditTransport {
  final Map<String, dynamic>? okResponse;
  final Object? error;

  _FakeTransport.ok(this.okResponse) : error = null;
  _FakeTransport.fail(this.error) : okResponse = null;

  @override
  Future<Map<String, dynamic>> getJson(String path,
      {Map<String, dynamic>? queryParams}) async {
    if (error != null) throw error!;
    return okResponse!;
  }
}

// ── Helpers ────────────────────────────────────────────────────────────────

Map<String, dynamic> _entryJson({
  String id = 'a1',
  String action = 'execute',
  String? skillName = 'web_search',
  String source = 'agent',
  String createdAt = '2026-06-04T12:00:00Z',
}) =>
    {
      'id': id,
      'action': action,
      'skill_name': skillName,
      'result_summary': null,
      'source': source,
      'created_at': createdAt,
    };

AuditNotifier _makeNotifier(AuditTransport t) =>
    AuditNotifier(AuditRepository(t));

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  group('AuditNotifier.load', () {
    test('sets isLoading then populates entries on success', () async {
      final t = _FakeTransport.ok({
        'data': [
          _entryJson(id: 'e1', action: 'execute'),
          _entryJson(id: 'e2', action: 'denied'),
        ],
        'count': 2,
      });
      final n = _makeNotifier(t);

      await n.load();

      expect(n.state.isLoading, isFalse);
      expect(n.state.error, isNull);
      expect(n.state.entries, hasLength(2));
      expect(n.state.entries[0].id, 'e1');
      expect(n.state.entries[1].id, 'e2');
    });

    test('sets error and clears isLoading on failure', () async {
      final t = _FakeTransport.fail(Exception('network down'));
      final n = _makeNotifier(t);

      await n.load();

      expect(n.state.isLoading, isFalse);
      expect(n.state.error, isNotNull);
      expect(n.state.entries, isEmpty);
    });

    test('clears previous error on a successful retry', () async {
      // First load fails.
      final fail = _FakeTransport.fail(Exception('timeout'));
      final n = _makeNotifier(fail);
      await n.load();
      expect(n.state.error, isNotNull);

      // Swap to a healthy transport — wrap notifier in the same instance.
      final ok = _FakeTransport.ok({
        'data': [_entryJson(id: 'e1')],
        'count': 1,
      });
      final n2 = AuditNotifier(AuditRepository(ok));
      await n2.load();
      expect(n2.state.error, isNull);
      expect(n2.state.entries, hasLength(1));
    });

    test('populates an empty list when server returns no entries', () async {
      final t = _FakeTransport.ok({'data': [], 'count': 0});
      final n = _makeNotifier(t);
      await n.load();

      expect(n.state.entries, isEmpty);
      expect(n.state.error, isNull);
      expect(n.state.isLoading, isFalse);
    });
  });

  group('AuditNotifier.refresh', () {
    test('re-fetches entries on refresh', () async {
      final t = _FakeTransport.ok({
        'data': [_entryJson(id: 'fresh')],
        'count': 1,
      });
      final n = _makeNotifier(t);

      await n.refresh();

      expect(n.state.entries, hasLength(1));
      expect(n.state.entries.first.id, 'fresh');
    });
  });

  group('AuditNotifier.setFilter', () {
    test('filters filteredEntries to the selected action', () async {
      final t = _FakeTransport.ok({
        'data': [
          _entryJson(id: 'e1', action: 'execute'),
          _entryJson(id: 'e2', action: 'denied'),
          _entryJson(id: 'e3', action: 'execute'),
        ],
        'count': 3,
      });
      final n = _makeNotifier(t);
      await n.load();

      n.setFilter('execute');

      expect(n.state.activeFilter, 'execute');
      expect(n.state.filteredEntries, hasLength(2));
      expect(n.state.filteredEntries.map((e) => e.id), containsAll(['e1', 'e3']));
    });

    test('toggles filter off when same action is passed twice', () async {
      final t = _FakeTransport.ok({
        'data': [_entryJson(id: 'e1', action: 'execute')],
        'count': 1,
      });
      final n = _makeNotifier(t);
      await n.load();

      n.setFilter('execute');
      expect(n.state.activeFilter, 'execute');

      n.setFilter('execute'); // toggle off
      expect(n.state.activeFilter, isNull);
      expect(n.state.filteredEntries, hasLength(1)); // all entries
    });

    test('switching filter replaces the previous one', () async {
      final t = _FakeTransport.ok({
        'data': [
          _entryJson(id: 'e1', action: 'execute'),
          _entryJson(id: 'e2', action: 'denied'),
        ],
        'count': 2,
      });
      final n = _makeNotifier(t);
      await n.load();

      n.setFilter('execute');
      n.setFilter('denied'); // replace

      expect(n.state.activeFilter, 'denied');
      expect(n.state.filteredEntries, hasLength(1));
      expect(n.state.filteredEntries.first.id, 'e2');
    });

    test('clearFilter resets to null / all entries', () async {
      final t = _FakeTransport.ok({
        'data': [
          _entryJson(id: 'e1', action: 'execute'),
          _entryJson(id: 'e2', action: 'denied'),
        ],
        'count': 2,
      });
      final n = _makeNotifier(t);
      await n.load();

      n.setFilter('denied');
      n.clearFilter();

      expect(n.state.activeFilter, isNull);
      expect(n.state.filteredEntries, hasLength(2));
    });
  });

  group('AuditState.filteredEntries', () {
    test('returns all entries when activeFilter is null', () {
      final entries = [
        const AuditEntry(
          id: 'e1',
          action: 'execute',
          source: 'agent',
          createdAt: '2026-06-04T00:00:00Z',
        ),
        const AuditEntry(
          id: 'e2',
          action: 'denied',
          source: 'policy',
          createdAt: '2026-06-04T01:00:00Z',
        ),
      ];
      final state = AuditState(entries: entries);
      expect(state.filteredEntries, hasLength(2));
    });

    test('filters to matching action when activeFilter is set', () {
      final entries = [
        const AuditEntry(
          id: 'e1',
          action: 'execute',
          source: 'agent',
          createdAt: '2026-06-04T00:00:00Z',
        ),
        const AuditEntry(
          id: 'e2',
          action: 'approved',
          source: 'user',
          createdAt: '2026-06-04T01:00:00Z',
        ),
        const AuditEntry(
          id: 'e3',
          action: 'execute',
          source: 'agent',
          createdAt: '2026-06-04T02:00:00Z',
        ),
      ];
      final state = AuditState(entries: entries, activeFilter: 'execute');
      expect(state.filteredEntries, hasLength(2));
      expect(state.filteredEntries.map((e) => e.id), containsAll(['e1', 'e3']));
    });

    test('returns empty when filter matches nothing', () {
      final entries = [
        const AuditEntry(
          id: 'e1',
          action: 'execute',
          source: 'agent',
          createdAt: '2026-06-04T00:00:00Z',
        ),
      ];
      final state = AuditState(entries: entries, activeFilter: 'denied');
      expect(state.filteredEntries, isEmpty);
    });
  });
}
