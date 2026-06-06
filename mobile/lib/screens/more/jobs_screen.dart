import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../ui/ui.dart';

/// Jobs power surface — stub filled by a dedicated agent (Phase C).
class JobsScreen extends ConsumerWidget {
  const JobsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return const LzScaffold(
      appBar: LzAppBar(title: 'Jobs'),
      body: LzEmptyState(
        icon: Icons.work_outline,
        title: 'Jobs — coming soon',
        hint: 'Gig pipeline, matched jobs, and proposals tracked here.',
      ),
    );
  }
}
