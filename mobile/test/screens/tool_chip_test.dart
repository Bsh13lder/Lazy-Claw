// ToolChip: status-driven glyphs (spinner / check / error / schedule),
// display-name label with ellipsis, 3-minute stall guard, and the always-on
// tap that opens the tool detail bottom sheet.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lazyclaw_mobile/chat/chat_message.dart';
import 'package:lazyclaw_mobile/screens/chat/tool_chip.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Future<void> _pump(WidgetTester tester, ToolActivity t) async {
  await tester.pumpWidget(MaterialApp(
    theme: buildAppTheme(),
    home: Scaffold(body: ToolChip(activity: t)),
  ));
}

void main() {
  testWidgets('running chip shows a spinner', (tester) async {
    await _pump(
      tester,
      const ToolActivity(name: 'web_search', args: {}),
    );
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
  });

  testWidgets('done chip shows the check glyph, never a spinner',
      (tester) async {
    await _pump(
      tester,
      const ToolActivity(
        name: 'web_search',
        args: {},
        resultPreview: 'found 3',
        status: ToolStatus.done,
      ),
    );
    expect(find.byIcon(Icons.check_circle_outline), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
  });

  testWidgets('error chip shows the error glyph', (tester) async {
    await _pump(
      tester,
      const ToolActivity(
        name: 'browser',
        args: {},
        status: ToolStatus.error,
      ),
    );
    expect(find.byIcon(Icons.error_outline), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
  });

  testWidgets('interrupted and unknown chips show a muted schedule glyph',
      (tester) async {
    await _pump(
      tester,
      const ToolActivity(
        name: 'browser',
        args: {},
        status: ToolStatus.interrupted,
      ),
    );
    expect(find.byIcon(Icons.schedule), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);

    await _pump(
      tester,
      const ToolActivity(
        name: 'browser',
        args: {},
        status: ToolStatus.unknown,
      ),
    );
    expect(find.byIcon(Icons.schedule), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing,
        reason: 'history chips (status unknown) must never spin');
  });

  testWidgets('label prefers displayName and is ellipsis-guarded',
      (tester) async {
    await _pump(
      tester,
      const ToolActivity(
        name: 'mcp__upwork__get_conversation_messages',
        displayName: 'Reading Upwork messages',
        args: {},
        status: ToolStatus.done,
      ),
    );
    expect(find.text('Reading Upwork messages'), findsOneWidget);
    expect(find.text('mcp__upwork__get_conversation_messages'), findsNothing);

    final label =
        tester.widget<Text>(find.text('Reading Upwork messages'));
    expect(label.maxLines, 1);
    expect(label.overflow, TextOverflow.ellipsis);
  });

  testWidgets('label falls back to the raw name without a displayName',
      (tester) async {
    await _pump(
      tester,
      const ToolActivity(
        name: 'web_search',
        args: {},
        status: ToolStatus.done,
      ),
    );
    expect(find.text('web_search'), findsOneWidget);
  });

  testWidgets('stall guard swaps the spinner for a schedule icon after 3 min',
      (tester) async {
    await _pump(
      tester,
      const ToolActivity(name: 'browser', args: {}),
    );
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    expect(find.byIcon(Icons.schedule), findsNothing);

    await tester.pump(const Duration(minutes: 3, seconds: 1));

    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.byIcon(Icons.schedule), findsOneWidget);
  });

  testWidgets('a chip update resets the stall clock', (tester) async {
    await _pump(
      tester,
      const ToolActivity(name: 'browser', args: {}, toolCallId: 'tc1'),
    );
    await tester.pump(const Duration(minutes: 2));
    // Fresh content (result landed) — no stall, straight to done.
    await _pump(
      tester,
      const ToolActivity(
        name: 'browser',
        args: {},
        toolCallId: 'tc1',
        resultPreview: 'ok',
        status: ToolStatus.done,
      ),
    );
    await tester.pump(const Duration(minutes: 2));
    expect(find.byIcon(Icons.check_circle_outline), findsOneWidget);
    expect(find.byIcon(Icons.schedule), findsNothing);
  });

  // ── Detail sheet ───────────────────────────────────────────────────────────

  testWidgets('tapping a RUNNING chip opens the detail sheet', (tester) async {
    await _pump(
      tester,
      const ToolActivity(
        name: 'web_search',
        displayName: 'Searching the web',
        args: {'query': 'lazyclaw'},
      ),
    );
    await tester.tap(find.byType(ToolChip));
    // Bounded pumps — the running spinner animates forever, so
    // pumpAndSettle would never settle.
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));

    // Raw name (small/muted) + pretty-printed arguments.
    expect(find.text('web_search'), findsOneWidget);
    expect(find.textContaining('"query"'), findsOneWidget);
    expect(find.textContaining('lazyclaw'), findsOneWidget);
  });

  testWidgets('detail sheet shows status, arguments JSON and result',
      (tester) async {
    await _pump(
      tester,
      const ToolActivity(
        name: 'web_search',
        displayName: 'Searching the web',
        args: {'query': 'x', 'count': 3},
        resultPreview: 'found 3 results',
        status: ToolStatus.done,
      ),
    );
    await tester.tap(find.byType(ToolChip));
    await tester.pumpAndSettle();

    expect(find.text('Done'), findsOneWidget);
    expect(find.textContaining('"count"'), findsOneWidget);
    expect(find.textContaining('found 3 results'), findsOneWidget);
  });

  testWidgets('detail sheet degrades gracefully with no args and no result',
      (tester) async {
    await _pump(
      tester,
      const ToolActivity(
        name: 'recall_memories',
        args: {},
        status: ToolStatus.unknown,
      ),
    );
    await tester.tap(find.byType(ToolChip));
    await tester.pumpAndSettle();

    expect(find.text('No arguments captured'), findsOneWidget);
    expect(find.text('No result captured'), findsOneWidget);
  });

  // 2026-08-20: three parallel `agent` dispatches all rendered as identical
  // "Dispatch Agent" chips — visually indistinguishable, so sequential OR
  // parallel runs both read as one chip that never settles. The chip must
  // carry its target so each dispatch has an identity.

  testWidgets('agent chip appends the agent_type from args', (tester) async {
    await _pump(
      tester,
      const ToolActivity(
        name: 'agent',
        displayName: 'Dispatch Agent',
        args: {'agent_type': 'browser', 'task': 'check the blog'},
        status: ToolStatus.running,
      ),
    );
    expect(find.text('Dispatch Agent · browser'), findsOneWidget);
  });

  testWidgets('non-agent chips ignore stray agent_type-less args',
      (tester) async {
    await _pump(
      tester,
      const ToolActivity(
        name: 'web_search',
        args: {'query': 'x'},
        status: ToolStatus.done,
      ),
    );
    expect(find.text('web_search'), findsOneWidget);
  });
}
