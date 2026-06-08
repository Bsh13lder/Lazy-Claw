import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/note.dart';
import '../../providers/notes_provider.dart';
import 'note_card.dart';
import 'note_create_sheet.dart';
import 'note_detail_screen.dart';

/// Open the "new note" bottom sheet and create the note on confirm.
///
/// Shared single source of truth for the create flow so the standalone Notes
/// screen, the Notes segment inside the Tasks tab, and the empty-state button
/// all behave identically. Reuses [NoteCreateSheet] + [notesProvider].
Future<void> showCreateNoteFlow(BuildContext context, WidgetRef ref) async {
  final result = await NoteCreateSheet.show(context);
  if (result == null || !context.mounted) return;
  await ref.read(notesProvider.notifier).createNote(
        title: result.title.isEmpty ? null : result.title,
        content: result.content,
      );
}

/// The Notes content — search field + list + detail navigation — WITHOUT the
/// surrounding scaffold/app-bar/FAB/banner.
///
/// This is the reusable core that both [NotesScreen] (standalone, kept for the
/// error-state test + back-compat) and the Tasks tab's Notes segment render.
/// The hosting screen owns the chrome (app bar title, create FAB, offline /
/// degraded banners) so Notes nests cleanly under either.
class NotesBody extends ConsumerStatefulWidget {
  const NotesBody({super.key});

  @override
  ConsumerState<NotesBody> createState() => _NotesBodyState();
}

class _NotesBodyState extends ConsumerState<NotesBody> {
  final _searchCtrl = TextEditingController();

  @override
  void initState() {
    super.initState();
    Future.microtask(() => ref.read(notesProvider.notifier).load());
  }

  @override
  void dispose() {
    _searchCtrl.dispose();
    super.dispose();
  }

  // ── Search (local — works fully offline) ───────────────────────────────────

  void _onSearchChanged(String value) {
    if (value.isEmpty) {
      ref.read(notesProvider.notifier).load();
    }
  }

  void _onSearchSubmitted(String value) {
    final q = value.trim();
    if (q.isEmpty) {
      ref.read(notesProvider.notifier).load();
    } else {
      ref.read(notesProvider.notifier).search(q);
    }
  }

  // ── Refresh (pull-to-refresh) ───────────────────────────────────────────────

  Future<void> _refresh() async {
    final q = _searchCtrl.text.trim();
    if (q.isNotEmpty) {
      await ref.read(notesProvider.notifier).search(q);
    } else {
      await ref.read(notesProvider.notifier).load();
    }
  }

  // ── Detail ──────────────────────────────────────────────────────────────────

  Future<void> _openNote(Note note) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => NoteDetailScreen(note: note),
      ),
    );
    if (mounted) await _refresh();
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(notesProvider);

    // Surface load/save errors as a snackbar.
    ref.listen<NotesState>(notesProvider, (prev, next) {
      if (next.error != null && next.error != prev?.error) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(next.error!),
            action: SnackBarAction(
              label: 'Dismiss',
              onPressed: () => ref.read(notesProvider.notifier).clearError(),
            ),
          ),
        );
      }
    });

    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.lg,
            AppSpacing.md,
            AppSpacing.lg,
            AppSpacing.sm,
          ),
          child: LzSearchField(
            controller: _searchCtrl,
            hint: 'Search notes…',
            onChanged: _onSearchChanged,
            onSubmitted: _onSearchSubmitted,
          ),
        ),
        Expanded(child: _buildBody(state)),
      ],
    );
  }

  Widget _buildBody(NotesState state) {
    // Loading skeleton — only on the first instant cache read.
    if (state.isLoading && state.notes.isEmpty && state.error == null) {
      return LzSkeleton.list(
        count: 5,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.md,
        ),
      );
    }

    // Error state — nothing cached + a load error → real Retry.
    if (state.notes.isEmpty && state.error != null) {
      return LzErrorState(
        message: state.error!,
        onRetry: () => ref.read(notesProvider.notifier).load(),
      );
    }

    // Empty state.
    if (!state.isLoading && state.notes.isEmpty && state.error == null) {
      final isSearch = state.searchQuery.isNotEmpty;
      return LzEmptyState(
        icon: isSearch ? Icons.search_off_rounded : Icons.notes_rounded,
        title: isSearch
            ? 'No results for "${state.searchQuery}"'
            : 'No notes yet',
        hint: isSearch
            ? 'Try a different keyword.'
            : 'Tap + to write your first note.',
        actionLabel: isSearch ? null : 'New note',
        actionIcon: isSearch ? null : Icons.add_rounded,
        onAction: isSearch ? null : () => showCreateNoteFlow(context, ref),
      );
    }

    // Populated list.
    return LzRefresh(
      onRefresh: _refresh,
      child: ListView.builder(
        padding: const EdgeInsets.only(top: AppSpacing.xs, bottom: 96),
        itemCount: state.sortedNotes.length,
        itemBuilder: (context, index) {
          final note = state.sortedNotes[index];
          return NoteCard(
            note: note,
            pendingSync: state.dirtyIds.contains(note.id),
            onTap: () => _openNote(note),
            onDelete: () => ref.read(notesProvider.notifier).deleteNote(note.id),
          );
        },
      ),
    );
  }
}
