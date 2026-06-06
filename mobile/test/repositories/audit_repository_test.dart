import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/audit_repository.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

class _FakeAuditTransport implements AuditTransport {
  String? lastPath;
  Map<String, dynamic>? lastQueryParams;

  /// Response returned by [getJson].
  Map<String, dynamic> response;

  _FakeAuditTransport(this.response);

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async {
    lastPath = path;
    lastQueryParams = queryParams;
    return response;
  }
}

// ── Fixtures ───────────────────────────────────────────────────────────────

Map<String, dynamic> _entry({
  String id = 'a1',
  String action = 'execute',
  String? skillName = 'web_search',
  String? resultSummary,
  String source = 'agent',
  String createdAt = '2026-06-04T12:00:00Z',
}) =>
    {
      'id': id,
      'action': action,
      'skill_name': skillName,
      'result_summary': resultSummary,
      'source': source,
      'created_at': createdAt,
    };

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  group('AuditRepository.listAudit', () {
    // ── Happy-path ──────────────────────────────────────────────────────────

    test('GETs /api/permissions/audit and returns parsed entries', () async {
      final t = _FakeAuditTransport({
        'data': [
          _entry(id: 'e1', action: 'execute'),
          _entry(id: 'e2', action: 'denied'),
        ],
        'count': 2,
      });
      final repo = AuditRepository(t);
      final entries = await repo.listAudit();

      expect(t.lastPath, '/api/permissions/audit');
      expect(entries, hasLength(2));
      expect(entries[0].id, 'e1');
      expect(entries[0].action, 'execute');
      expect(entries[1].id, 'e2');
      expect(entries[1].action, 'denied');
    });

    test('sends no query params when all opts are null', () async {
      final t = _FakeAuditTransport({'data': [], 'count': 0});
      await AuditRepository(t).listAudit();
      expect(t.lastQueryParams, isNull);
    });

    test('passes action filter as query param', () async {
      final t = _FakeAuditTransport({'data': [], 'count': 0});
      await AuditRepository(t).listAudit(action: 'denied');
      expect(t.lastQueryParams, containsPair('action', 'denied'));
    });

    test('passes since as query param', () async {
      final t = _FakeAuditTransport({'data': [], 'count': 0});
      await AuditRepository(t).listAudit(since: '2026-06-01T00:00:00Z');
      expect(t.lastQueryParams, containsPair('since', '2026-06-01T00:00:00Z'));
    });

    test('passes limit as string query param', () async {
      final t = _FakeAuditTransport({'data': [], 'count': 0});
      await AuditRepository(t).listAudit(limit: 50);
      expect(t.lastQueryParams, containsPair('limit', '50'));
    });

    test('sends all provided query params together', () async {
      final t = _FakeAuditTransport({'data': [], 'count': 0});
      await AuditRepository(t).listAudit(
        action: 'execute',
        since: '2026-01-01T00:00:00Z',
        limit: 100,
      );
      expect(t.lastQueryParams, containsPair('action', 'execute'));
      expect(t.lastQueryParams, containsPair('since', '2026-01-01T00:00:00Z'));
      expect(t.lastQueryParams, containsPair('limit', '100'));
    });

    // ── Field mapping ───────────────────────────────────────────────────────

    test('maps all AuditEntry fields correctly', () async {
      final t = _FakeAuditTransport({
        'data': [
          _entry(
            id: 'x9',
            action: 'approved',
            skillName: 'send_email',
            resultSummary: 'Email sent to alice@example.com',
            source: 'user',
            createdAt: '2026-06-04T08:30:00Z',
          ),
        ],
        'count': 1,
      });
      final entries = await AuditRepository(t).listAudit();
      final e = entries.first;

      expect(e.id, 'x9');
      expect(e.action, 'approved');
      expect(e.skillName, 'send_email');
      expect(e.resultSummary, 'Email sent to alice@example.com');
      expect(e.source, 'user');
      expect(e.createdAt, '2026-06-04T08:30:00Z');
    });

    test('handles null skill_name gracefully', () async {
      final t = _FakeAuditTransport({
        'data': [_entry(skillName: null)],
        'count': 1,
      });
      final entries = await AuditRepository(t).listAudit();
      expect(entries.first.skillName, isNull);
    });

    test('handles null result_summary gracefully', () async {
      final t = _FakeAuditTransport({
        'data': [_entry(resultSummary: null)],
        'count': 1,
      });
      final entries = await AuditRepository(t).listAudit();
      expect(entries.first.resultSummary, isNull);
    });

    // ── Edge cases ──────────────────────────────────────────────────────────

    test('returns empty list when data key is missing', () async {
      final t = _FakeAuditTransport({'count': 0});
      final entries = await AuditRepository(t).listAudit();
      expect(entries, isEmpty);
    });

    test('returns empty list when data is an empty array', () async {
      final t = _FakeAuditTransport({'data': [], 'count': 0});
      final entries = await AuditRepository(t).listAudit();
      expect(entries, isEmpty);
    });

    test('tolerates missing id field — falls back to empty string', () async {
      final t = _FakeAuditTransport({
        'data': [
          {'action': 'execute', 'source': 'agent', 'created_at': '2026-06-04T00:00:00Z'},
        ],
        'count': 1,
      });
      final entries = await AuditRepository(t).listAudit();
      expect(entries.first.id, '');
    });

    test('parses a large batch (50 entries) without error', () async {
      final batch =
          List.generate(50, (i) => _entry(id: 'e$i', action: 'execute'));
      final t = _FakeAuditTransport({'data': batch, 'count': 50});
      final entries = await AuditRepository(t).listAudit(limit: 50);
      expect(entries, hasLength(50));
      expect(entries[49].id, 'e49');
    });
  });

  group('AuditEntry.fromJson', () {
    test('constructs from minimal required fields', () {
      final e = AuditEntry.fromJson({
        'id': 'min1',
        'action': 'denied',
        'source': 'policy',
        'created_at': '2026-06-01T00:00:00Z',
      });
      expect(e.id, 'min1');
      expect(e.action, 'denied');
      expect(e.skillName, isNull);
      expect(e.resultSummary, isNull);
      expect(e.source, 'policy');
    });

    test('coerces numeric id to string', () {
      final e = AuditEntry.fromJson({
        'id': 42,
        'action': 'execute',
        'source': 'agent',
        'created_at': '2026-06-01T00:00:00Z',
      });
      expect(e.id, '42');
    });
  });
}
