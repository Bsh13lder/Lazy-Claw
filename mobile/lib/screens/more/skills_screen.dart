import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../ui/ui.dart';

/// Skills power surface — stub filled by a dedicated agent (Phase C).
class SkillsScreen extends ConsumerWidget {
  const SkillsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return const LzScaffold(
      appBar: LzAppBar(title: 'Skills'),
      body: LzEmptyState(
        icon: Icons.auto_awesome_outlined,
        title: 'Skills — coming soon',
        hint: 'Browse, enable, and manage agent skills here.',
      ),
    );
  }
}
