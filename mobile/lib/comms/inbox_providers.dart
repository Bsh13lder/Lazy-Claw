import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../providers/auth_provider.dart'; // apiClientProvider
import 'inbox_models.dart';
import 'inbox_repository.dart';

final inboxRepositoryProvider = Provider<InboxRepository>((ref) {
  final client = ref.watch(apiClientProvider);
  return InboxRepository(DioInboxTransport(client));
});

final inboxChannelFilterProvider = StateProvider<String?>((ref) => null);

final inboxThreadsProvider = FutureProvider<List<InboxThread>>((ref) async {
  final repo = ref.watch(inboxRepositoryProvider);
  final channel = ref.watch(inboxChannelFilterProvider);
  return repo.fetchThreads(channel: channel);
});

final inboxMessagesProvider =
    FutureProvider.family<List<InboxMessage>, String>((ref, threadId) async {
  final repo = ref.watch(inboxRepositoryProvider);
  return repo.fetchMessages(threadId);
});

// Set by a tapped channel_message notification to deep-link a thread.
final pendingInboxThreadProvider = StateProvider<String?>((ref) => null);
