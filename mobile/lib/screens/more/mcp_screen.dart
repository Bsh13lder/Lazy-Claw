import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../ui/ui.dart';

/// MCP power surface — stub filled by a dedicated agent (Phase C).
class McpScreen extends ConsumerWidget {
  const McpScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return const LzScaffold(
      appBar: LzAppBar(title: 'MCP'),
      body: LzEmptyState(
        icon: Icons.device_hub_outlined,
        title: 'MCP — coming soon',
        hint: 'Connected MCP servers and their exposed tools listed here.',
      ),
    );
  }
}
