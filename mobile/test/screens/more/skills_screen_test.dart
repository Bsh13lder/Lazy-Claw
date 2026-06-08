import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/skills_provider.dart';
import 'package:lazyclaw_mobile/repositories/skills_repository.dart';
import 'package:lazyclaw_mobile/screens/more/skills_screen.dart';
import 'package:lazyclaw_mobile/ui/app_theme.dart';
import 'package:lazyclaw_mobile/ui/components/lz_chip.dart';
import 'package:lazyclaw_mobile/ui/components/lz_empty_state.dart';
import 'package:lazyclaw_mobile/ui/components/lz_search_field.dart';
import 'package:lazyclaw_mobile/ui/components/lz_skeleton.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

class _FakeTransport implements SkillsTransport {
  final List<Map<String, dynamic>> skills;
  bool fail;
  /// When non-null, the transport holds until this future completes before
  /// returning — lets tests observe the loading state.
  Future<void>? gate;

  _FakeTransport({this.skills = const [], this.fail = false, this.gate});

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async {
    if (gate != null) await gate;
    if (fail) throw Exception('transport_error');
    return {'skills': skills};
  }

  @override
  Future<Map<String, dynamic>> patchJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    if (fail) throw Exception('patch_error');
    return {'status': 'ok'};
  }
}

// ── Helpers ────────────────────────────────────────────────────────────────

Map<String, dynamic> _s({
  String id = 's1',
  String name = 'web_search',
  String skillType = 'builtin',
  bool enabled = true,
  String? category,
}) =>
    {
      'id': id,
      'name': name,
      'description': 'Does $name stuff',
      'skill_type': skillType,
      'enabled': enabled,
      'category': category,
    };

Widget _host(_FakeTransport transport) {
  return ProviderScope(
    overrides: [
      skillsRepositoryProvider.overrideWithValue(
        SkillsRepository(transport),
      ),
    ],
    child: MaterialApp(
      theme: buildAppTheme(),
      home: const SkillsScreen(),
    ),
  );
}

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  group('SkillsScreen — structure', () {
    testWidgets('renders LzAppBar with title "Skills"', (tester) async {
      await tester.pumpWidget(_host(_FakeTransport()));
      await tester.pump();
      expect(find.text('Skills'), findsOneWidget);
    });

    testWidgets('renders LzSearchField', (tester) async {
      await tester.pumpWidget(_host(_FakeTransport()));
      await tester.pump();
      expect(find.byType(LzSearchField), findsOneWidget);
    });

    testWidgets('shows LzSkeleton while loading', (tester) async {
      // Gate keeps the transport blocked so we can observe the loading state.
      final completer = Completer<void>();
      final transport = _FakeTransport(gate: completer.future);
      await tester.pumpWidget(_host(transport));
      // One frame: postFrameCallback fires → notifier.load() called → sets
      // isLoading=true; transport is still blocked.
      await tester.pump();
      // The skeleton list is in the tree while loading=true.
      expect(find.byType(LzSkeleton), findsWidgets);
      // Unblock so the test process can finish cleanly.
      completer.complete();
      await tester.pumpAndSettle();
    });
  });

  group('SkillsScreen — populated list', () {
    testWidgets('renders skill names after load', (tester) async {
      final transport = _FakeTransport(
        skills: [
          _s(id: '1', name: 'browser_read', skillType: 'builtin'),
          _s(id: '2', name: 'send_email', skillType: 'instruction'),
        ],
      );
      await tester.pumpWidget(_host(transport));
      await tester.pumpAndSettle();

      expect(find.text('browser_read'), findsOneWidget);
      expect(find.text('send_email'), findsOneWidget);
    });

    testWidgets('renders LzChip per skill with category label', (tester) async {
      final transport = _FakeTransport(
        skills: [_s(id: '1', skillType: 'builtin')],
      );
      await tester.pumpWidget(_host(transport));
      await tester.pumpAndSettle();

      // Each tile has one LzChip with the category label.
      expect(find.byType(LzChip), findsWidgets);
      expect(find.text('builtin'), findsOneWidget);
    });

    testWidgets('groups skills by category with section header', (tester) async {
      final transport = _FakeTransport(
        skills: [
          _s(id: '1', skillType: 'builtin'),
          _s(id: '2', skillType: 'instruction'),
        ],
      );
      await tester.pumpWidget(_host(transport));
      await tester.pumpAndSettle();

      // LzSection renders the title uppercased.
      expect(find.text('BUILTIN (1)'), findsOneWidget);
      expect(find.text('INSTRUCTION (1)'), findsOneWidget);
    });

    testWidgets('renders a Switch for each skill', (tester) async {
      final transport = _FakeTransport(
        skills: [
          _s(id: '1', enabled: true),
          _s(id: '2', enabled: false),
        ],
      );
      await tester.pumpWidget(_host(transport));
      await tester.pumpAndSettle();

      final switches = tester.widgetList<Switch>(find.byType(Switch)).toList();
      expect(switches, hasLength(2));
      expect(switches[0].value, isTrue);
      expect(switches[1].value, isFalse);
    });

    testWidgets('registered count label shows correct total', (tester) async {
      final transport = _FakeTransport(
        skills: [_s(id: '1'), _s(id: '2'), _s(id: '3')],
      );
      await tester.pumpWidget(_host(transport));
      await tester.pumpAndSettle();

      expect(find.text('3 registered'), findsOneWidget);
    });
  });

  group('SkillsScreen — empty state', () {
    testWidgets('shows LzEmptyState when list is empty', (tester) async {
      await tester.pumpWidget(_host(_FakeTransport(skills: [])));
      await tester.pumpAndSettle();

      expect(find.byType(LzEmptyState), findsOneWidget);
    });
  });

  group('SkillsScreen — search filter', () {
    testWidgets('search input filters visible skills', (tester) async {
      final transport = _FakeTransport(
        skills: [
          _s(id: '1', name: 'browser_read'),
          _s(id: '2', name: 'send_email'),
        ],
      );
      await tester.pumpWidget(_host(transport));
      await tester.pumpAndSettle();

      // Type into the search field.
      await tester.enterText(find.byType(LzSearchField), 'browser');
      await tester.pump();

      expect(find.text('browser_read'), findsOneWidget);
      expect(find.text('send_email'), findsNothing);
    });

    testWidgets('filtered count label updates', (tester) async {
      final transport = _FakeTransport(
        skills: [
          _s(id: '1', name: 'browser_read'),
          _s(id: '2', name: 'send_email'),
        ],
      );
      await tester.pumpWidget(_host(transport));
      await tester.pumpAndSettle();

      await tester.enterText(find.byType(LzSearchField), 'browser');
      await tester.pump();

      expect(find.text('1 of 2'), findsOneWidget);
    });
  });

  group('SkillsScreen — toggle', () {
    testWidgets('tapping switch optimistically changes its value',
        (tester) async {
      final transport = _FakeTransport(
        skills: [_s(id: 'tog1', enabled: true)],
      );
      await tester.pumpWidget(_host(transport));
      await tester.pumpAndSettle();

      final initialSwitch = tester.widget<Switch>(find.byType(Switch).first);
      expect(initialSwitch.value, isTrue);

      await tester.tap(find.byType(Switch).first);
      await tester.pumpAndSettle();

      final flippedSwitch = tester.widget<Switch>(find.byType(Switch).first);
      expect(flippedSwitch.value, isFalse);
    });
  });

  group('SkillsScreen — error handling', () {
    testWidgets('shows error banner when transport fails', (tester) async {
      final transport = _FakeTransport(fail: true);
      await tester.pumpWidget(_host(transport));
      await tester.pumpAndSettle();

      // Error message is shown somewhere in the tree.
      expect(find.textContaining('transport_error'), findsOneWidget);
    });
  });
}
