import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/notifications_repository.dart';
import 'package:lazyclaw_mobile/screens/notifications/notification_detail_sheet.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

ServerNotification _notif({String body = 'Full body text', String title = 'Ping'}) {
  return ServerNotification.fromJson({
    'id': 'n1',
    'kind': 'push',
    'title': title,
    'body': body,
    'created_at': '2026-08-09 13:12:00',
    'severity': 'normal',
    'repeat_count': 1,
  });
}

Widget _host(ServerNotification n) {
  return MaterialApp(
    theme: buildAppTheme(),
    home: Scaffold(
      body: Builder(
        builder: (context) => Center(
          child: ElevatedButton(
            onPressed: () => showNotificationDetailSheet(
              context,
              n,
              accent: AppColors.accent,
              icon: Icons.notifications_none,
            ),
            child: const Text('open'),
          ),
        ),
      ),
    ),
  );
}

void main() {
  testWidgets('sheet shows full title and body', (tester) async {
    final longBody = 'Line one of the ping.\n' * 12;
    await tester.pumpWidget(_host(_notif(body: longBody)));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    expect(find.text('Ping'), findsOneWidget);
    // Full body rendered as selectable text (not the 2-line list ellipsis).
    expect(
      find.byWidgetPredicate(
        (w) => w is SelectableText && (w.data ?? '').contains('Line one'),
      ),
      findsOneWidget,
    );
    expect(find.text('push'), findsOneWidget);
  });

  testWidgets('empty body shows the whole-message hint', (tester) async {
    await tester.pumpWidget(_host(_notif(body: '')));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    expect(
      find.textContaining('this is the whole message'),
      findsOneWidget,
    );
  });
}
