import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/note.dart';
import '../../providers/lazybrain_provider.dart';
import '../../repositories/lazybrain_repository.dart';
import '../../ui/ui.dart';
import '../notes/note_detail_screen.dart';
import 'brain/ask_brain_sheet.dart';
import 'brain/brain_note_card.dart';
import 'brain/journal_section.dart';
import 'brain/tags_section.dart';

/// Brain power surface — a mobile window into LazyBrain (the agent's PKM).
///
/// Top: semantic search (submit-to-search) + an "Ask Brain" RAG sheet.
/// Sections: journal timeline (14 days), pinned notes, tag counts with an
/// inline tag drill-down. Online-only (server is authoritative + encrypted).
///
/// Route: /more/brain
class BrainScreen extends ConsumerStatefulWidget {
  const BrainScreen({super.key});

  @override
  ConsumerState<BrainScreen> createState() => _BrainScreenState();
}

class _BrainScreenState extends ConsumerState<BrainScreen> {
  final _searchCtrl = TextEditingController();

  bool _searching = false;
  SemanticSearchResult? _searchResult;
  String? _searchError;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(brainProvider.notifier).load();
    });
  }

  @override
  void dispose() {
    _searchCtrl.dispose();
    super.dispose();
  }

  // ── Actions ───────────────────────────────────────────────────────────────

  Future<void> _onRefresh() => ref.read(brainProvider.notifier).load();

  Future<void> _runSearch(String query) async {
    final q = query.trim();
    if (q.isEmpty) {
      _clearSearch();
      return;
    }
    setState(() {
      _searching = true;
      _searchError = null;
    });
    try {
      final result = await ref.read(brainProvider.notifier).search(q);
      if (!mounted) return;
      setState(() {
        _searchResult = result;
        _searching = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _searchError = e.toString();
        _searching = false;
      });
    }
  }

  void _clearSearch() {
    setState(() {
      _searchResult = null;
      _searchError = null;
      _searching = false;
    });
  }

  void _openAskSheet() {
    showAskBrainSheet(
      context,
      ask: (question) => ref.read(brainProvider.notifier).ask(question),
    );
  }

  void _openNote(Note note) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => NoteDetailScreen(note: note),
      ),
    );
  }

  // ── Build ─────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(brainProvider);
    return LzScaffold(
      appBar: LzAppBar(
        title: 'Brain',
        actions: [
          LzIconButton(
            icon: Icons.auto_awesome_outlined,
            tooltip: 'Ask Brain',
            accent: true,
            onPressed: _openAskSheet,
          ),
        ],
      ),
      body: _buildBody(state),
    );
  }

  Widget _buildBody(BrainState state) {
    if (state.isLoading && state.isEmpty) {
      return LzSkeleton.list(count: 6);
    }

    if (state.error != null && state.isEmpty) {
      return LzErrorState(
        message: 'Could not reach the brain.\n${state.error}',
        onRetry: _onRefresh,
      );
    }

    final searchActive =
        _searching || _searchResult != null || _searchError != null;

    return LzRefresh(
      onRefresh: _onRefresh,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.lg,
          AppSpacing.md,
          AppSpacing.lg,
          AppSpacing.xxxl,
        ),
        children: [
          LzSearchField(
            controller: _searchCtrl,
            hint: 'Search the brain…',
            onSubmitted: _runSearch,
            onChanged: (value) {
              // Clearing the field (✕ or backspace) exits search mode.
              if (value.isEmpty && searchActive) _clearSearch();
            },
          ),
          const SizedBox(height: AppSpacing.lg),
          if (searchActive)
            _SearchResults(
              searching: _searching,
              result: _searchResult,
              error: _searchError,
              onRetry: () => _runSearch(_searchCtrl.text),
              onClear: () {
                _searchCtrl.clear();
                _clearSearch();
              },
              onOpenNote: _openNote,
            )
          else ...[
            JournalSection(notes: state.journal),
            const SizedBox(height: AppSpacing.xl),
            _PinnedSection(notes: state.pinned, onOpenNote: _openNote),
            const SizedBox(height: AppSpacing.xl),
            TagsSection(onOpenNote: _openNote),
          ],
        ],
      ),
    );
  }
}

// ── Search results block ────────────────────────────────────────────────────

class _SearchResults extends StatelessWidget {
  const _SearchResults({
    required this.searching,
    required this.result,
    required this.error,
    required this.onRetry,
    required this.onClear,
    required this.onOpenNote,
  });

  final bool searching;
  final SemanticSearchResult? result;
  final String? error;
  final VoidCallback onRetry;
  final VoidCallback onClear;
  final void Function(Note note) onOpenNote;

  @override
  Widget build(BuildContext context) {
    return LzSection(
      title: 'Results',
      action: LzChip(
        label: 'Clear',
        icon: Icons.close,
        dense: true,
        onTap: onClear,
      ),
      child: _buildContent(),
    );
  }

  Widget _buildContent() {
    if (searching) {
      return const Column(
        children: [
          LzSkeleton(height: 72, borderRadius: AppRadii.rLg),
          SizedBox(height: AppSpacing.sm),
          LzSkeleton(height: 72, borderRadius: AppRadii.rLg),
          SizedBox(height: AppSpacing.sm),
          LzSkeleton(height: 72, borderRadius: AppRadii.rLg),
        ],
      );
    }

    if (error != null) {
      return LzErrorState(
        message: 'Search failed.\n$error',
        icon: Icons.search_off_outlined,
        onRetry: onRetry,
      );
    }

    final hits = result?.hits ?? const <SemanticHit>[];
    if (hits.isEmpty) {
      return const LzEmptyState(
        icon: Icons.search_off_outlined,
        title: 'Nothing matched',
        hint: 'Try different words — the brain searches by meaning.',
      );
    }

    return Column(
      children: [
        for (var i = 0; i < hits.length; i++) ...[
          if (i > 0) const SizedBox(height: AppSpacing.sm),
          BrainNoteCard(
            note: hits[i].note,
            score: hits[i].score,
            onTap: () => onOpenNote(hits[i].note),
          ),
        ],
      ],
    );
  }
}

// ── Pinned section ──────────────────────────────────────────────────────────

class _PinnedSection extends StatelessWidget {
  const _PinnedSection({required this.notes, required this.onOpenNote});

  final List<Note> notes;
  final void Function(Note note) onOpenNote;

  @override
  Widget build(BuildContext context) {
    return LzSection(
      title: 'Pinned',
      child: notes.isEmpty
          ? Text(
              'No pinned notes — pin important notes to keep them here.',
              style: AppText.body.copyWith(color: AppColors.textMuted),
            )
          : Column(
              children: [
                for (var i = 0; i < notes.length; i++) ...[
                  if (i > 0) const SizedBox(height: AppSpacing.sm),
                  BrainNoteCard(
                    note: notes[i],
                    onTap: () => onOpenNote(notes[i]),
                  ),
                ],
              ],
            ),
    );
  }
}
