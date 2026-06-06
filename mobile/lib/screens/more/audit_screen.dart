import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../ui/ui.dart';

/// Audit power surface — stub filled by a dedicated agent (Phase C).
class AuditScreen extends ConsumerWidget {
  const AuditScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return const LzScaffold(
      appBar: LzAppBar(title: 'Audit'),
      body: LzEmptyState(
        icon: Icons.policy_outlined,
        title: 'Audit — coming soon',
        hint: 'Permission log and skill activity history shown here.',
      ),
    );
  }
}
