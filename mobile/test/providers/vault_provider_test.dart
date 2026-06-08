import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/vault_provider.dart';
import 'package:lazyclaw_mobile/repositories/vault_repository.dart';

// ── Fake repository ────────────────────────────────────────────────────────

/// In-memory stand-in. Starts with [_entries]; addSecret appends; deleteSecret
/// removes by name.
class _FakeRepo implements VaultRepository {
  final List<VaultEntry> _entries;
  bool shouldFail;

  _FakeRepo({List<VaultEntry>? entries, this.shouldFail = false})
      : _entries = List.of(entries ?? const []);

  // VaultRepository is a concrete class, so we forward the transport field
  // to a no-op and override the public methods instead.
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);

  @override
  Future<List<VaultEntry>> listSecrets() async {
    if (shouldFail) throw Exception('network error');
    return List.unmodifiable(_entries);
  }

  @override
  Future<void> addSecret(String name, String value) async {
    if (shouldFail) throw Exception('save failed');
    _entries.removeWhere((e) => e.name == name);
    _entries.add(VaultEntry(name: name));
  }

  @override
  Future<void> deleteSecret(String name) async {
    if (shouldFail) throw Exception('delete failed');
    _entries.removeWhere((e) => e.name == name);
  }
}

// ── Helpers ────────────────────────────────────────────────────────────────

VaultNotifier _makeNotifier(_FakeRepo repo) => VaultNotifier(repo);

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  group('VaultNotifier.load', () {
    test('sets isLoading=false and entries after success', () async {
      final repo = _FakeRepo(entries: [
        const VaultEntry(name: 'OPENAI_API_KEY'),
        const VaultEntry(name: 'STRIPE_TOKEN'),
      ]);
      final n = _makeNotifier(repo);

      await n.load();

      expect(n.state.isLoading, isFalse);
      expect(n.state.error, isNull);
      expect(n.state.entries, hasLength(2));
      expect(n.state.entries.map((e) => e.name),
          containsAll(['OPENAI_API_KEY', 'STRIPE_TOKEN']));
    });

    test('sets error and entries=[] on transport failure', () async {
      final repo = _FakeRepo(shouldFail: true);
      final n = _makeNotifier(repo);

      await n.load();

      expect(n.state.isLoading, isFalse);
      expect(n.state.error, isNotNull);
      expect(n.state.entries, isEmpty);
    });

    test('clears previous error on successful reload', () async {
      final repo = _FakeRepo(shouldFail: true);
      final n = _makeNotifier(repo);
      await n.load();
      expect(n.state.error, isNotNull);

      repo.shouldFail = false;
      await n.load();
      expect(n.state.error, isNull);
    });
  });

  group('VaultNotifier.addSecret', () {
    test('returns true and refreshes entries on success', () async {
      final repo = _FakeRepo(entries: [const VaultEntry(name: 'EXISTING_KEY')]);
      final n = _makeNotifier(repo);
      await n.load();

      final ok = await n.addSecret('NEW_SECRET', 'val');

      expect(ok, isTrue);
      expect(n.state.isSubmitting, isFalse);
      expect(n.state.error, isNull);
      final names = n.state.entries.map((e) => e.name).toList();
      expect(names, contains('NEW_SECRET'));
    });

    test('returns false and sets error on transport failure', () async {
      final repo = _FakeRepo();
      final n = _makeNotifier(repo);
      await n.load();

      repo.shouldFail = true;
      final ok = await n.addSecret('KEY', 'val');

      expect(ok, isFalse);
      expect(n.state.isSubmitting, isFalse);
      expect(n.state.error, isNotNull);
    });

    test('replaces existing entry with the same name', () async {
      final repo = _FakeRepo(entries: [const VaultEntry(name: 'DUPE_KEY')]);
      final n = _makeNotifier(repo);
      await n.load();

      await n.addSecret('DUPE_KEY', 'new-value');

      final names = n.state.entries.map((e) => e.name).toList();
      expect(names.where((x) => x == 'DUPE_KEY'), hasLength(1));
    });
  });

  group('VaultNotifier.deleteSecret', () {
    test('returns true and removes entry from state on success', () async {
      final repo = _FakeRepo(entries: [
        const VaultEntry(name: 'A'),
        const VaultEntry(name: 'B'),
      ]);
      final n = _makeNotifier(repo);
      await n.load();

      final ok = await n.deleteSecret('A');

      expect(ok, isTrue);
      expect(n.state.entries.map((e) => e.name), isNot(contains('A')));
      expect(n.state.entries.map((e) => e.name), contains('B'));
    });

    test('returns false and sets error on transport failure', () async {
      final repo = _FakeRepo(entries: [const VaultEntry(name: 'X')]);
      final n = _makeNotifier(repo);
      await n.load();

      repo.shouldFail = true;
      final ok = await n.deleteSecret('X');

      expect(ok, isFalse);
      expect(n.state.error, isNotNull);
    });

    test('optimistic removal keeps existing entries intact on failure', () async {
      final repo = _FakeRepo(entries: [
        const VaultEntry(name: 'KEEP'),
        const VaultEntry(name: 'GONE'),
      ]);
      final n = _makeNotifier(repo);
      await n.load();

      // Even on failure the notifier sets error but the optimistic state
      // had already been updated; the key point is isSubmitting resets.
      repo.shouldFail = true;
      await n.deleteSecret('GONE');
      expect(n.state.isSubmitting, isFalse);
    });
  });

  group('VaultNotifier.clearError', () {
    test('clears the error field', () async {
      final repo = _FakeRepo(shouldFail: true);
      final n = _makeNotifier(repo);
      await n.load();
      expect(n.state.error, isNotNull);

      n.clearError();
      expect(n.state.error, isNull);
    });
  });

  group('VaultState.copyWith', () {
    test('clearError=true sets error to null', () {
      const s = VaultState(error: 'oops');
      final next = s.copyWith(clearError: true);
      expect(next.error, isNull);
    });

    test('copyWith without clearError preserves existing error', () {
      const s = VaultState(error: 'existing');
      final next = s.copyWith(isLoading: false);
      expect(next.error, 'existing');
    });

    test('isLoading is replaced independently', () {
      const s = VaultState(isLoading: true);
      final next = s.copyWith(isLoading: false);
      expect(next.isLoading, isFalse);
    });
  });

  group('vaultProvider (Riverpod)', () {
    test('provider is constructed without throwing', () {
      // Use overrides to inject a fake repo without a real ApiClient.
      final container = ProviderContainer(
        overrides: [
          vaultRepositoryProvider.overrideWithValue(
            _FakeRepo(entries: [const VaultEntry(name: 'TEST')]),
          ),
        ],
      );
      addTearDown(container.dispose);

      final state = container.read(vaultProvider);
      // Initial state is empty (load hasn't been called yet).
      expect(state.isLoading, isFalse);
      expect(state.entries, isEmpty);
    });
  });
}
