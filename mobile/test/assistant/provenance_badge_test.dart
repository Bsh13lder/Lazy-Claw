import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/assistant/lazy_assistant_controller.dart';
import 'package:lazyclaw_mobile/assistant/widgets/mic_state_indicator.dart';
import 'package:lazyclaw_mobile/assistant/widgets/provenance_badge.dart';
import 'package:lazyclaw_mobile/ui/app_theme.dart';

Widget _host(Widget child) =>
    MaterialApp(theme: buildAppTheme(), home: Scaffold(body: child));

void main() {
  group('ProvenanceBadge', () {
    testWidgets('cloud → "Cloud" label + "Processed in the cloud" semantics',
        (tester) async {
      await tester.pumpWidget(
          _host(const ProvenanceBadge(source: TurnSource.cloud)));
      expect(find.text('Cloud'), findsOneWidget);
      expect(
        find.bySemanticsLabel('Processed in the cloud'),
        findsOneWidget,
      );
    });

    testWidgets(
        'onDevice → "On-device" label + "Processed on device" semantics',
        (tester) async {
      await tester.pumpWidget(
          _host(const ProvenanceBadge(source: TurnSource.onDevice)));
      expect(find.text('On-device'), findsOneWidget);
      expect(
        find.bySemanticsLabel('Processed on device'),
        findsOneWidget,
      );
    });
  });

  group('MicStateIndicator', () {
    testWidgets('live → "Microphone live" semantics', (tester) async {
      await tester.pumpWidget(_host(const MicStateIndicator(live: true)));
      expect(find.bySemanticsLabel('Microphone live'), findsOneWidget);
    });

    testWidgets('muted → "Microphone muted" semantics', (tester) async {
      await tester.pumpWidget(_host(const MicStateIndicator(live: false)));
      expect(find.bySemanticsLabel('Microphone muted'), findsOneWidget);
    });
  });
}
