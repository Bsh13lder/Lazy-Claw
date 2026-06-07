import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../providers/notes_provider.dart';
// reachableProvider + dbHealthProvider live in tasks_provider (shared
// offline-first infra).
import '../providers/tasks_provider.dart'
    show reachableProvider, dbHealthProvider;
import 'notes/notes_body.dart';
import 'storage_banners.dart';

export 'notes/note_detail_screen.dart' show NoteDetailScreen;
export 'notes/notes_body.dart' show NotesBody, showCreateNoteFlow;

/// Standalone Notes screen — the chrome (app bar + create FAB + offline /
/// degraded banners) around the reusable [NotesBody].
///
/// Notes also appears as a top segment inside the Tasks tab (see
/// [TasksScreen]); both render the same [NotesBody]. This standalone screen is
/// kept for direct embedding / tests.
class NotesScreen extends ConsumerWidget {
  const NotesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final offline = !ref.watch(reachableProvider);
    final degraded = ref.watch(dbHealthProvider).isDegraded;

    return LzScaffold(
      appBar: LzAppBar(
        title: 'Notes',
        large: true,
        gradientTitle: true,
        actions: [
          LzIconButton(
            icon: Icons.add_rounded,
            tooltip: 'New note',
            accent: true,
            filled: true,
            onPressed: () => showCreateNoteFlow(context, ref),
          ),
        ],
      ),
      banner: buildStorageBanners(
        context,
        offline: offline,
        degraded: degraded,
        onRetry: () => ref.read(notesProvider.notifier).load(),
      ),
      body: const NotesBody(),
      floatingActionButton: FloatingActionButton(
        onPressed: () => showCreateNoteFlow(context, ref),
        tooltip: 'New note',
        backgroundColor: AppColors.accent,
        foregroundColor: AppColors.onAccent,
        elevation: 4,
        child: const Icon(Icons.add_rounded),
      ),
    );
  }
}
