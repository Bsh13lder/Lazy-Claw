import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../ui/ui.dart';

/// Memory power surface — stub filled by a dedicated agent (Phase C).
class MemoryScreen extends ConsumerWidget {
  const MemoryScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return const LzScaffold(
      appBar: LzAppBar(title: 'Memory'),
      body: LzEmptyState(
        icon: Icons.psychology_outlined,
        title: 'Memory — coming soon',
        hint: 'Agent memory and encrypted personal facts live here.',
      ),
    );
  }
}
