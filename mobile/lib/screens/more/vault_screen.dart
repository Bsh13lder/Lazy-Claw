import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../ui/ui.dart';

/// Vault power surface — stub filled by a dedicated agent (Phase C).
class VaultScreen extends ConsumerWidget {
  const VaultScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return const LzScaffold(
      appBar: LzAppBar(title: 'Vault'),
      body: LzEmptyState(
        icon: Icons.lock_outline,
        title: 'Vault — coming soon',
        hint: 'Encrypted credentials and API keys stored here.',
      ),
    );
  }
}
