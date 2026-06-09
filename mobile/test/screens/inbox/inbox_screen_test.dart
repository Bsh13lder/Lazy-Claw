import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/comms/inbox_models.dart';
import 'package:lazyclaw_mobile/comms/inbox_providers.dart';
import 'package:lazyclaw_mobile/screens/inbox/inbox_screen.dart';

void main() {
  testWidgets('renders thread rows', (tester) async {
    await tester.pumpWidget(ProviderScope(
      overrides: [
        inboxThreadsProvider.overrideWith((ref) async => [
          const InboxThread(
            id: 't1',
            channel: 'whatsapp',
            contactHandle: '+1',
            contactName: 'Alice',
            lastPreview: 'see you',
            unreadCount: 2,
            lastActivity: '2026-06-09T10:00:00Z',
            updatedAt: '2026-06-09T10:00:00Z',
          ),
        ]),
      ],
      child: const MaterialApp(home: InboxScreen()),
    ));
    await tester.pumpAndSettle();
    expect(find.text('Alice'), findsOneWidget);
    expect(find.text('see you'), findsOneWidget);
  });

  testWidgets('empty state when no threads', (tester) async {
    await tester.pumpWidget(ProviderScope(
      overrides: [
        inboxThreadsProvider.overrideWith((ref) async => <InboxThread>[]),
      ],
      child: const MaterialApp(home: InboxScreen()),
    ));
    await tester.pumpAndSettle();
    expect(find.textContaining('No messages'), findsOneWidget);
  });
}
