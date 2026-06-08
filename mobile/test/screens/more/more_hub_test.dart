import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/ui/app_theme.dart';
import 'package:lazyclaw_mobile/screens/more/more_hub.dart';
import 'package:lazyclaw_mobile/screens/more/skills_screen.dart';
import 'package:lazyclaw_mobile/screens/more/specialists_screen.dart';
import 'package:lazyclaw_mobile/screens/more/vault_screen.dart';
import 'package:lazyclaw_mobile/screens/more/memory_screen.dart';
import 'package:lazyclaw_mobile/screens/more/jobs_screen.dart';
import 'package:lazyclaw_mobile/screens/more/watchers_screen.dart';
import 'package:lazyclaw_mobile/screens/more/mcp_screen.dart';
import 'package:lazyclaw_mobile/screens/more/audit_screen.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

Widget _host(Widget child) => ProviderScope(
      child: MaterialApp(theme: buildAppTheme(), home: child),
    );

void main() {
  group('MoreHubScreen', () {
    testWidgets('renders "Power tools" title', (tester) async {
      await tester.pumpWidget(_host(const MoreHubScreen()));
      expect(find.text('Power tools'), findsOneWidget);
    });

    testWidgets('renders all 8 tile labels', (tester) async {
      // Taller surface so all 8 tiles fit within the lazy ListView's viewport
      // + cache extent (the 8th tile would otherwise be off-screen).
      await tester.binding.setSurfaceSize(const Size(800, 1400));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      await tester.pumpWidget(_host(const MoreHubScreen()));
      for (final label in [
        'Skills',
        'Specialists',
        'Vault',
        'Memory',
        'Jobs',
        'Watchers',
        'MCP',
        'Audit',
      ]) {
        expect(find.text(label), findsOneWidget, reason: '$label tile missing');
      }
    });
  });

  group('Stub screens render without error', () {
    for (final pair in <(String, Widget)>[
      ('SkillsScreen', const SkillsScreen()),
      ('SpecialistsScreen', const SpecialistsScreen()),
      ('VaultScreen', const VaultScreen()),
      ('MemoryScreen', const MemoryScreen()),
      ('JobsScreen', const JobsScreen()),
      ('WatchersScreen', const WatchersScreen()),
      ('McpScreen', const McpScreen()),
      ('AuditScreen', const AuditScreen()),
    ]) {
      testWidgets('${pair.$1} smoke test', (tester) async {
        await tester.pumpWidget(_host(pair.$2));
        expect(tester.takeException(), isNull);
      });
    }
  });
}
