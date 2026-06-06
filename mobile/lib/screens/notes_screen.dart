import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../models/note.dart';
import '../providers/notes_provider.dart';
// reachableProvider lives in tasks_provider (shared offline-first infra).
import '../providers/tasks_provider.dart' show reachableProvider;
import 'notes/note_card.dart';
import 'notes/note_create_sheet.dart';
import 'notes/note_detail_screen.dart';

export 'notes/note_detail_screen.dart' show NoteDetailScreen;

// ── Notes list screen ──────────────────────────────────────────────────────

class NotesScreen extends ConsumerStatefulWidget {
  const NotesScreen({super.key});

  @override
  ConsumerState<NotesScreen> createState() => _NotesScreenState();
}

class _NotesScreenState extends ConsumerState<NotesScreen> {
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

  // ── Search (local — works fully offline) ──────────────────────────────────

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

  // ── Refresh (pull-to-refresh) ──────────────────────────────────────────────

  Future<void> _refresh() async {
    final q = _searchCtrl.text.trim();
    if (q.isNotEmpty) {
      await ref.read(notesProvider.notifier).search(q);
    } else {
      await ref.read(notesProvider.notifier).load();
    }
  }

  // ── Create ────────────────────────────────────────────────────────────────

  Future<void> _openCreateSheet() async {
    final result = await NoteCreateSheet.show(context);
    if (result == null || !mounted) return;
    await ref.read(notesProvider.notifier).createNote(
          title: result.title.isEmpty ? null : result.title,
          content: result.content,
        );
  }

  // ── Detail ────────────────────────────────────────────────────────────────

  Future<void> _openNote(Note note) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => NoteDetailScreen(note: note),
      ),
    );
    // Reload in case the note was edited inside the detail screen.
    if (mounted) await _refresh();
  }

  // ── Build ─────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(notesProvider);
    final offline = !ref.watch(reachableProvider);

    // Show snack-bar on error.
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
            onPressed: _openCreateSheet,
          ),
        ],
      ),
      banner: offline ? const LzBanner.offline(safeAreaTop: false) : null,
      body: Column(
        children: [
          // Search field pinned below the app bar.
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
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: _openCreateSheet,
        tooltip: 'New note',
        backgroundColor: AppColors.accent,
        foregroundColor: AppColors.onAccent,
        elevation: 4,
        child: const Icon(Icons.add_rounded),
      ),
    );
  }

  Widget _buildBody(NotesState state) {
    // Loading skeleton.
    if (state.isLoading && state.notes.isEmpty) {
      return LzSkeleton.list(
        count: 5,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.md,
        ),
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
        onAction: isSearch ? null : _openCreateSheet,
      );
    }

    // Populated list.
    return LzRefresh(
      onRefresh: _refresh,
      child: ListView.builder(
        padding: const EdgeInsets.only(
          top: AppSpacing.xs,
          bottom: 96, // above FAB
        ),
        itemCount: state.sortedNotes.length,
        itemBuilder: (context, index) {
          final note = state.sortedNotes[index];
          return NoteCard(
            note: note,
            pendingSync: state.dirtyIds.contains(note.id),
            onTap: () => _openNote(note),
            onDelete: () =>
                ref.read(notesProvider.notifier).deleteNote(note.id),
          );
        },
      ),
    );
  }
}
