import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../ui/ui.dart';

/// Watchers power surface — stub filled by a dedicated agent (Phase C).
class WatchersScreen extends ConsumerWidget {
  const WatchersScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return const LzScaffold(
      appBar: LzAppBar(title: 'Watchers'),
      body: LzEmptyState(
        icon: Icons.visibility_outlined,
        title: 'Watchers — coming soon',
        hint: 'Live site monitors and change alerts configured here.',
      ),
    );
  }
}
