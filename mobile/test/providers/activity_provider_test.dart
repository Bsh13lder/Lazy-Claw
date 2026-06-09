import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/activity_provider.dart';
import 'package:lazyclaw_mobile/repositories/activity_repository.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

class _Transport implements ActivityTransport {
  Map<String, dynamic>? next;
  Object? throwError;
  int calls = 0;

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async {
    calls++;
    if (throwError != null) throw throwError!;
    return next ?? const {};
  }
}

Map<String, dynamic> _oneRunning() => {
      'active': [
        {'task_id': 'a1', 'name': 'Working', 'lane': 'main', 'status': 'running'},
      ],
    };

void main() {
  group('ActivityNotifier', () {
    test('load() populates the snapshot and clears loading', () async {
      final t = _Transport()..next = _oneRunning();
      final n = ActivityNotifier(ActivityRepository(t));

      await n.load();

      expect(n.state.isLoading, isFalse);
      expect(n.state.error, isNull);
      expect(n.state.snapshot.running, hasLength(1));
      expect(n.state.hasData, isTrue);
    });

    test('load() surfaces an error when the fetch fails', () async {
      final t = _Transport()..throwError = StateError('boom');
      final n = ActivityNotifier(ActivityRepository(t));

      await n.load();

      expect(n.state.isLoading, isFalse);
      expect(n.state.error, isNotNull);
      expect(n.state.hasData, isFalse);
    });

    test('refresh() keeps prior data and stays silent on a transient failure',
        () async {
      final t = _Transport()..next = _oneRunning();
      final n = ActivityNotifier(ActivityRepository(t));
      await n.load();
      expect(n.state.hasData, isTrue);

      // Next poll fails — must NOT wipe the snapshot or raise an error banner.
      t.throwError = StateError('network blip');
      await n.refresh();

      expect(n.state.hasData, isTrue, reason: 'previous snapshot retained');
      expect(n.state.error, isNull, reason: 'no error while data is on screen');
    });

    test('refresh() never flips the loading flag', () async {
      final t = _Transport()..next = _oneRunning();
      final n = ActivityNotifier(ActivityRepository(t));
      await n.refresh();
      expect(n.state.isLoading, isFalse);
      expect(n.state.snapshot.running, hasLength(1));
    });
  });
}
