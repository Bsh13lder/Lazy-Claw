import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../models/note.dart';
import '../../../providers/lazybrain_provider.dart';
import '../../../ui/ui.dart';
import 'brain_note_card.dart';

/// Tags section — a wrap of count-labeled chips. Tapping a tag loads its
/// notes inline below the wrap (with a selected chip acting as the
/// clear/back affordance).
class TagsSection extends ConsumerWidget {
  const TagsSection({super.key, required this.onOpenNote});

  final void Function(Note note) onOpenNote;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(brainProvider);
    final notifier = ref.read(brainProvider.notifier);

    return LzSection(
      title: 'Tags',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (state.tags.isEmpty)
            Text(
              'No tags yet — tags appear as the brain files notes.',
              style: AppText.body.copyWith(color: AppColors.textMuted),
            )
          else
            Wrap(
              spacing: AppSpacing.xs,
              runSpacing: AppSpacing.xs,
              children: state.tags
                  .map(
                    (t) => LzChip(
                      label: '${t.tag} (${t.count})',
                      selected: state.activeTag == t.tag,
                      icon: state.activeTag == t.tag ? Icons.close : null,
                      onTap: () => state.activeTag == t.tag
                          ? notifier.clearTag()
                          : notifier.selectTag(t.tag),
                    ),
                  )
                  .toList(),
            ),
          if (state.activeTag != null) ...[
            const SizedBox(height: AppSpacing.lg),
            _TagNotesList(state: state, notifier: notifier, onOpenNote: onOpenNote),
          ],
        ],
      ),
    );
  }
}

// ── Drill-down list under the chip wrap ─────────────────────────────────────

class _TagNotesList extends StatelessWidget {
  const _TagNotesList({
    required this.state,
    required this.notifier,
    required this.onOpenNote,
  });

  final BrainState state;
  final BrainNotifier notifier;
  final void Function(Note note) onOpenNote;

  @override
  Widget build(BuildContext context) {
    if (state.isTagLoading) {
      return const Column(
        children: [
          LzSkeleton(height: 64, borderRadius: AppRadii.rLg),
          SizedBox(height: AppSpacing.sm),
          LzSkeleton(height: 64, borderRadius: AppRadii.rLg),
        ],
      );
    }

    if (state.tagError != null) {
      return LzErrorState(
        message: 'Could not load #${state.activeTag} notes.\n${state.tagError}',
        onRetry: () => notifier.selectTag(state.activeTag!),
      );
    }

    if (state.tagNotes.isEmpty) {
      return Text(
        'No notes carry #${state.activeTag}.',
        style: AppText.body.copyWith(color: AppColors.textMuted),
      );
    }

    return Column(
      children: [
        for (var i = 0; i < state.tagNotes.length; i++) ...[
          if (i > 0) const SizedBox(height: AppSpacing.sm),
          BrainNoteCard(
            note: state.tagNotes[i],
            onTap: () => onOpenNote(state.tagNotes[i]),
          ),
        ],
      ],
    );
  }
}
