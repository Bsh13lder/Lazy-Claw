/// Tag editor bottom-sheet for documents — extracted from documents_list_view.dart
/// to keep that file under the 800-line limit.
///
/// [TagEditorSheet.show] opens the sheet and returns the edited tag list (or
/// null when dismissed). Used by the Documents list's long-press tag editor.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

// ── Tag editor bottom-sheet ────────────────────────────────────────────────────

class TagEditorSheet extends StatefulWidget {
  const TagEditorSheet({
    super.key,
    required this.current,
    required this.knownTags,
  });

  final List<String> current;
  final Set<String> knownTags;

  /// Shows the sheet and returns the new tag list, or null when dismissed.
  static Future<List<String>?> show(
    BuildContext context, {
    required List<String> current,
    required Set<String> knownTags,
  }) {
    return LzBottomSheet.show<List<String>>(
      context,
      title: 'Tags',
      builder: (ctx) => TagEditorSheet(
        current: current,
        knownTags: knownTags,
      ),
    );
  }

  @override
  State<TagEditorSheet> createState() => TagEditorSheetState();
}

class TagEditorSheetState extends State<TagEditorSheet> {
  late List<String> _tags;
  final TextEditingController _ctrl = TextEditingController();
  final FocusNode _focusNode = FocusNode();

  static const int _maxTagLength = 40;

  @override
  void initState() {
    super.initState();
    _tags = List<String>.from(widget.current);
  }

  @override
  void dispose() {
    _ctrl.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  void _addTag(String raw) {
    final tag = raw.trim();
    if (tag.isEmpty) return;
    final clamped =
        tag.length > _maxTagLength ? tag.substring(0, _maxTagLength) : tag;
    if (_tags.contains(clamped)) {
      _ctrl.clear();
      return;
    }
    setState(() {
      _tags = [..._tags, clamped];
    });
    _ctrl.clear();
  }

  void _removeTag(String tag) {
    setState(() {
      _tags = _tags.where((t) => t != tag).toList();
    });
  }

  void _save() {
    if (_ctrl.text.trim().isNotEmpty) _addTag(_ctrl.text);
    Navigator.of(context).pop(List<String>.unmodifiable(_tags));
  }

  @override
  Widget build(BuildContext context) {
    // Suggestions: known tags not yet on this document.
    final suggestions = widget.knownTags
        .where((t) => !_tags.contains(t))
        .toList()
      ..sort();

    return SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // ── Current tags ────────────────────────────────────────────────────
          if (_tags.isNotEmpty) ...[
            Wrap(
              spacing: AppSpacing.sm,
              runSpacing: AppSpacing.sm,
              children: [
                for (final tag in _tags)
                  _DeletableTagChip(
                    tag: tag,
                    onDelete: () => _removeTag(tag),
                  ),
              ],
            ),
            const SizedBox(height: AppSpacing.md),
          ],
          // ── Text field ──────────────────────────────────────────────────────
          Text(
            'Add tag',
            style: AppText.label.copyWith(color: AppColors.textSecondary),
          ),
          const SizedBox(height: AppSpacing.sm),
          TextField(
            controller: _ctrl,
            focusNode: _focusNode,
            style: AppText.body,
            cursorColor: AppColors.accent,
            textInputAction: TextInputAction.done,
            inputFormatters: [
              LengthLimitingTextInputFormatter(_maxTagLength),
            ],
            onSubmitted: _addTag,
            decoration: const InputDecoration(
              hintText: 'e.g. finance, Q3, draft',
            ),
          ),
          // ── Suggestions ─────────────────────────────────────────────────────
          if (suggestions.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.md),
            Text(
              'Suggested',
              style: AppText.caption.copyWith(color: AppColors.textMuted),
            ),
            const SizedBox(height: AppSpacing.sm),
            Wrap(
              spacing: AppSpacing.sm,
              runSpacing: AppSpacing.sm,
              children: [
                for (final tag in suggestions)
                  _SuggestionChip(
                    tag: tag,
                    onTap: () => _addTag(tag),
                  ),
              ],
            ),
          ],
          const SizedBox(height: AppSpacing.lg),
          // ── Actions ─────────────────────────────────────────────────────────
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              LzButton.ghost(
                label: 'Cancel',
                onPressed: () => Navigator.of(context).pop(),
              ),
              const SizedBox(width: AppSpacing.sm),
              LzButton.primary(
                label: 'Save',
                onPressed: _save,
              ),
            ],
          ),
        ],
      ),
    );
  }
}

// ── Deletable tag chip (inside the tag editor) ────────────────────────────────

class _DeletableTagChip extends StatelessWidget {
  const _DeletableTagChip({required this.tag, required this.onDelete});

  final String tag;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      borderRadius: AppRadii.rPill,
      child: InkWell(
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
            border: Border.all(
              color: AppColors.accent.withValues(alpha: 0.35),
            ),
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

// ── Suggestion chip ────────────────────────────────────────────────────────────

class _SuggestionChip extends StatelessWidget {
  const _SuggestionChip({required this.tag, required this.onTap});

  final String tag;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      borderRadius: AppRadii.rPill,
      child: InkWell(
        borderRadius: AppRadii.rPill,
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.md,
            vertical: AppSpacing.xs,
          ),
          decoration: BoxDecoration(
            color: AppColors.bgSurfaceElevated,
            borderRadius: AppRadii.rPill,
            border: Border.all(color: AppColors.borderDefault),
          ),
          child: Text(
            tag,
            style: AppText.caption.copyWith(
              color: AppColors.textSecondary,
              fontWeight: FontWeight.w600,
            ),
          ),
        ),
      ),
    );
  }
}
