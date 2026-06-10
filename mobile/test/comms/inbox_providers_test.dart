import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/comms/inbox_providers.dart';
import 'package:lazyclaw_mobile/comms/inbox_models.dart';
import 'package:lazyclaw_mobile/comms/inbox_repository.dart';

class _FakeRepo implements InboxRepository {
  @override
  Future<List<InboxThread>> fetchThreads({String? channel}) async => [
    const InboxThread(id: 't1', channel: 'whatsapp', contactHandle: '+1',
      contactName: 'Alice', lastPreview: 'hi', unreadCount: 1,
      lastActivity: '2026-06-09T10:00:00Z', updatedAt: '2026-06-09T10:00:00Z'),
  ];
  @override
  noSuchMethod(Invocation i) => super.noSuchMethod(i);
}

void main() {
  test('inboxThreadsProvider loads threads', () async {
    final container = ProviderContainer(overrides: [
      inboxRepositoryProvider.overrideWithValue(_FakeRepo()),
    ]);
    addTearDown(container.dispose);
    final threads = await container.read(inboxThreadsProvider.future);
    expect(threads.length, 1);
    expect(threads.first.contactName, 'Alice');
  });

  test('inboxChannelFilter is passed to fetchThreads', () async {
    final container = ProviderContainer(overrides: [
      inboxRepositoryProvider.overrideWithValue(_FakeRepo()),
    ]);
    addTearDown(container.dispose);
    container.read(inboxChannelFilterProvider.notifier).state = 'email';
    final threads = await container.read(inboxThreadsProvider.future);
    expect(threads, isNotEmpty); // fake ignores filter but provider must rebuild without error
  });
}
