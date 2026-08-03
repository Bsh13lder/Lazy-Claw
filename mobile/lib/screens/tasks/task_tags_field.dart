/// The task detail sheet's TAGS control: a compact summary chip that opens a
/// popup, plus the pure list transforms behind it.
///
/// WHY it stopped being a full section: TAGS used to be a label + a `Wrap` of
/// chips + a PERMANENTLY visible text field, three stacked blocks for a field
/// most edits never touch — in a sheet that is already too long to reach the
/// bottom of. It now sits beside PROJECT as one chip in the same pill style,
/// and the editing surface (chips + field) moved into a popup. Nothing was
/// lost: add, remove, trim, clamp and de-dup all still happen, they just
/// happen in [showTaskTagsSheet].
///
/// Extracted rather than grown into `task_detail_sheet.dart`, which was
/// already past this project's 800-line ceiling.
library;

import 'package:flutter/material.dart';

import '../../ui/ui.dart';

/// Hard cap on one tag's length. Was a private constant in the detail sheet.
const int kMaxTaskTagLength = 40;

/// Shown on the chip when the task has no tags. Reads as an offer, not as an
/// empty/broken control.
const String kTaskTagsEmptyLabel = '＋ Tags';

/// Stable keys, shared with the tests so a rename can't silently orphan them.
const Key kTaskTagsChipKey = Key('task-detail-tags-chip');
const Key kTaskTagsDoneKey = Key('task-detail-tags-done');

/// The key of the field inside the popup. UNCHANGED from when the field lived
/// inline on the sheet, so existing finders keep resolving.
const Key kTaskTagInputKey = Key('task-detail-tag-input');

// ── Pure list transforms ─────────────────────────────────────────────────────

/// [tags] plus [raw], trimmed and clamped to [kMaxTaskTagLength].
///
/// Blank input and exact duplicates are no-ops. De-dup deliberately runs AFTER
/// the clamp, so two over-long tags that differ only past the cap collapse to
/// one rather than becoming two identical-looking chips.
///
/// Always returns a NEW list — the caller's list is never mutated, so a list
/// already handed to a widget can't change under it.
List<String> tagsWithAdded(
  List<String> tags,
  String raw, {
  int maxLength = kMaxTaskTagLength,
}) {
  final tag = raw.trim();
  if (tag.isEmpty) return [...tags];
  final clamped = tag.length > maxLength ? tag.substring(0, maxLength) : tag;
  if (tags.contains(clamped)) return [...tags];
  return [...tags, clamped];
}

/// [tags] without [tag]. Always returns a new list (see [tagsWithAdded]).
List<String> tagsWithRemoved(List<String> tags, String tag) => [
  for (final t in tags)
    if (t != tag) t,
];

/// The compact summary rendered on the chip: an invitation when empty, the
/// tag itself when there is one, `first +N` when there are more.
String taskTagsChipLabel(List<String> tags) {
  if (tags.isEmpty) return kTaskTagsEmptyLabel;
  if (tags.length == 1) return tags.first;
  return '${tags.first} +${tags.length - 1}';
}

// ── The compact chip ─────────────────────────────────────────────────────────

/// The tappable TAGS control, deliberately shaped exactly like `ProjectChip`
/// so the two sit in one row and read as one pair of controls.
class TaskTagsChip extends StatelessWidget {
  const TaskTagsChip({
    super.key,
    required this.tags,
    required this.onTap,
    this.fieldKey = kTaskTagsChipKey,
  });

  final List<String> tags;
  final VoidCallback onTap;

  /// The [Key] applied to the tappable [InkWell] itself (distinct from [key],
  /// the widget's own key) — mirrors `ProjectChip.fieldKey`.
  final Key fieldKey;

  @override
  Widget build(BuildContext context) {
    final hasTags = tags.isNotEmpty;
    return Material(
      color: Colors.transparent,
      borderRadius: AppRadii.rPill,
      child: InkWell(
        key: fieldKey,
        onTap: onTap,
        borderRadius: AppRadii.rPill,
        child: Container(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.md,
            vertical: AppSpacing.xs + 2,
          ),
          decoration: BoxDecoration(
            color: AppColors.bgSurfaceElevated,
            borderRadius: AppRadii.rPill,
            border: Border.all(color: AppColors.borderDefault),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                Icons.tag_rounded,
                size: 15,
                color: hasTags ? AppColors.accent : AppColors.textMuted,
              ),
              const SizedBox(width: AppSpacing.sm),
              Text(
                taskTagsChipLabel(tags),
                style: AppText.caption.copyWith(
                  color: hasTags ? AppColors.textPrimary : AppColors.textMuted,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// A removable tag chip (tap to delete), shown inside the tags popup.
///
/// Was `_TaskTagChip` in `task_detail_sheet.dart`; moved verbatim (including
/// its `task-detail-tag-<tag>` key) so existing finders keep resolving.
class TaskTagChip extends StatelessWidget {
  const TaskTagChip({super.key, required this.tag, required this.onDelete});

  final String tag;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      borderRadius: AppRadii.rPill,
      child: InkWell(
        key: ValueKey('task-detail-tag-$tag'),
        borderRadius: AppRadii.rPill,
        onTap: onDelete,
        child: Container(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.md,
            AppSpacing.xs,
            AppSpacing.xs,
            AppSpacing.xs,
          ),
          decoration: BoxDecoration(
            color: AppColors.accent.withValues(alpha: 0.14),
            borderRadius: AppRadii.rPill,
            border: Border.all(color: AppColors.accent.withValues(alpha: 0.35)),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                tag,
                style: AppText.caption.copyWith(
                  color: AppColors.accent,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(width: AppSpacing.xs),
              Icon(
                Icons.close_rounded,
                size: 14,
                color: AppColors.accent.withValues(alpha: 0.7),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ── The popup ────────────────────────────────────────────────────────────────

/// Open the tag editor as a bottom sheet.
///
/// [controller] is owned by the CALLER, not by this sheet. That is deliberate:
/// the detail sheet's Save folds any un-submitted text in the tag field into
/// the list, and a controller that died with the popup would silently drop a
/// half-typed tag the moment the popup closed — a behavior regression against
/// the old always-on field.
///
/// [onChanged] fires on every mutation with a fresh list (never a mutated one),
/// so the caller's state — and the summary chip behind this sheet — stay in
/// step live. Like `showCommentsSheet`, this sheet lives on its own
/// route and therefore cannot see the caller's `setState`; it keeps a local
/// working copy seeded from [tags].
Future<void> showTaskTagsSheet(
  BuildContext context, {
  required List<String> tags,
  required TextEditingController controller,
  required ValueChanged<List<String>> onChanged,
}) {
  return LzBottomSheet.show<void>(
    context,
    title: 'Tags',
    builder: (_) => _TaskTagsSheetBody(
      tags: tags,
      controller: controller,
      onChanged: onChanged,
    ),
  );
}

class _TaskTagsSheetBody extends StatefulWidget {
  const _TaskTagsSheetBody({
    required this.tags,
    required this.controller,
    required this.onChanged,
  });

  final List<String> tags;
  final TextEditingController controller;
  final ValueChanged<List<String>> onChanged;

  @override
  State<_TaskTagsSheetBody> createState() => _TaskTagsSheetBodyState();
}

class _TaskTagsSheetBodyState extends State<_TaskTagsSheetBody> {
  late List<String> _tags;

  @override
  void initState() {
    super.initState();
    _tags = List.of(widget.tags);
  }

  void _apply(List<String> next) {
    setState(() => _tags = next);
    widget.onChanged(next);
  }

  void _add(String raw) {
    // Clearing only on non-blank input mirrors the old inline field exactly:
    // a stray Enter on an empty box left whatever was there alone.
    if (raw.trim().isEmpty) return;
    _apply(tagsWithAdded(_tags, raw));
    widget.controller.clear();
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (_tags.isNotEmpty) ...[
          Wrap(
            spacing: AppSpacing.sm,
            runSpacing: AppSpacing.sm,
            children: [
              for (final tag in _tags)
                TaskTagChip(
                  tag: tag,
                  onDelete: () => _apply(tagsWithRemoved(_tags, tag)),
                ),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
        ] else ...[
          const LzEmptyState(
            icon: Icons.tag_rounded,
            title: 'No tags yet',
            hint: 'Add one below — tap a tag to remove it.',
          ),
          const SizedBox(height: AppSpacing.md),
        ],
        LzTextField(
          controller: widget.controller,
          fieldKey: kTaskTagInputKey,
          hint: 'Add a tag',
          prefixIcon: Icons.label_outline,
          textInputAction: TextInputAction.done,
          onSubmitted: _add,
        ),
        const SizedBox(height: AppSpacing.lg),
        LzButton.primary(
          key: kTaskTagsDoneKey,
          label: 'Done',
          icon: Icons.check,
          expand: true,
          onPressed: () => Navigator.of(context).pop(),
        ),
      ],
    );
  }
}
