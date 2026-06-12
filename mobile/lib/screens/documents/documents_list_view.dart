import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../providers/documents_provider.dart';
import '../../repositories/documents_repository.dart';

// ── Pure filter helper (top-level so tests can import it directly) ────────────

/// Returns docs that have ALL [selectedTags] present (AND semantics).
/// When [selectedTags] is empty, returns [docs] unchanged.
List<DocMeta> filterByTags(List<DocMeta> docs, Set<String> selectedTags) {
  if (selectedTags.isEmpty) return docs;
  return docs
      .where((d) => selectedTags.every((t) => d.tags.contains(t)))
      .toList();
}

/// The list for one [DocKind] sub-tab: skeleton / error / empty / cards.
///
/// Network-only (the office suite is decrypted server-side per request), so this
/// shows loading + error states from the kit. Tapping a card calls [onOpen];
/// the empty-state action calls [onCreate]; per-card delete confirms first.
/// Long-pressing a card opens the tag editor sheet.
class DocumentsListView extends ConsumerStatefulWidget {
  const DocumentsListView({
    super.key,
    required this.kind,
    required this.onOpen,
    required this.onCreate,
  });

  final DocKind kind;
  final void Function(DocMeta meta) onOpen;
  final VoidCallback onCreate;

  @override
  ConsumerState<DocumentsListView> createState() => _DocumentsListViewState();
}

class _DocumentsListViewState extends ConsumerState<DocumentsListView> {
  /// Tags currently selected for filtering (AND semantics).
  Set<String> _selectedTags = {};

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(documentsListProvider(widget.kind).notifier).load();
    });
  }

  Future<void> _confirmDelete(DocMeta meta) async {
    final ok = await LzConfirm.show(
      context,
      title: 'Delete "${meta.name}"?',
      message: 'This permanently deletes the file. This cannot be undone.',
      confirmLabel: 'Delete',
      danger: true,
    );
    if (!ok || !mounted) return;
    await ref.read(documentsListProvider(widget.kind).notifier).delete(meta.id);
  }

  /// Opens the tag editor bottom-sheet for [meta]. Handles save routing:
  /// PDFs → setPdfTags; sheets/docs → fetch-then-save with CAS protection.
  Future<void> _editTags(DocMeta meta) async {
    final allKnownTags = ref
        .read(documentsListProvider(widget.kind))
        .items
        .expand((d) => d.tags)
        .toSet();

    final newTags = await _TagEditorSheet.show(
      context,
      current: meta.tags,
      knownTags: allKnownTags,
    );
    if (newTags == null || !mounted) return;

    final repo = ref.read(documentsRepositoryProvider);

    try {
      if (widget.kind == DocKind.pdf) {
        await repo.setPdfTags(meta.id, newTags);
      } else {
        await _saveTagsForDoc(repo, meta, newTags);
      }
      if (mounted) {
        ref.read(documentsListProvider(widget.kind).notifier).refresh();
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            backgroundColor: AppColors.bgSurfaceElevated,
            content: Text(
              "Couldn't save tags",
              style: AppText.body.copyWith(color: AppColors.error),
            ),
          ),
        );
      }
    }
  }

  /// Fetch-then-save for sheets/docs (keeps CAS safety). Retries once on
  /// [DocConflictException] with the server's current payload.
  Future<void> _saveTagsForDoc(
    DocumentsRepository repo,
    DocMeta meta,
    List<String> tags,
  ) async {
    Future<void> attempt(DocPayload detail) => repo.save(
          widget.kind,
          meta.id,
          detail.payload,
          tags: tags,
          baseUpdatedAt: detail.updatedAt,
        );

    final detail = await repo.getPayload(widget.kind, meta.id);
    try {
      await attempt(detail);
    } on DocConflictException catch (conflict) {
      // Retry once with the server's current version.
      await attempt(conflict.current);
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(documentsListProvider(widget.kind));
    final notifier = ref.read(documentsListProvider(widget.kind).notifier);

    // Surface errors as a snackbar (the list itself stays visible on refresh
    // errors; the empty-error case falls through to LzErrorState below).
    ref.listen<DocumentsListState>(documentsListProvider(widget.kind),
        (prev, next) {
      if (next.error != null &&
          next.error != prev?.error &&
          next.items.isNotEmpty) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            backgroundColor: AppColors.bgSurfaceElevated,
            content: Text(next.error!, style: AppText.body),
            action: SnackBarAction(
              label: 'Dismiss',
              textColor: AppColors.accent,
              onPressed: notifier.clearError,
            ),
          ),
        );
      }
    });

    if (state.isLoading && state.items.isEmpty && state.error == null) {
      return LzSkeleton.list(count: 5);
    }

    if (state.items.isEmpty && state.error != null) {
      return LzErrorState(message: state.error!, onRetry: notifier.load);
    }

    if (!state.isLoading && state.items.isEmpty) {
      return LzEmptyState(
        icon: _emptyIcon,
        title: 'No ${widget.kind.label.toLowerCase()} yet',
        hint: _emptyHint,
        actionLabel: _createLabel,
        actionIcon: widget.kind == DocKind.pdf
            ? Icons.upload_file_rounded
            : Icons.add_rounded,
        onAction: widget.onCreate,
      );
    }

    // Compute distinct sorted tags and filtered items.
    final allTags = state.items
        .expand((d) => d.tags)
        .toSet()
        .toList()
      ..sort();
    final filtered = filterByTags(state.items, _selectedTags);
    final hasAnyTags = allTags.isNotEmpty;

    return LzRefresh(
      onRefresh: notifier.refresh,
      child: CustomScrollView(
        slivers: [
          // ── Tag filter row ─────────────────────────────────────────────────
          if (hasAnyTags)
            SliverToBoxAdapter(
              child: _TagFilterRow(
                tags: allTags,
                selected: _selectedTags,
                onToggle: (tag) {
                  setState(() {
                    if (tag == null) {
                      _selectedTags = {};
                    } else if (_selectedTags.contains(tag)) {
                      _selectedTags = _selectedTags
                          .where((t) => t != tag)
                          .toSet();
                    } else {
                      _selectedTags = {..._selectedTags, tag};
                    }
                  });
                },
              ),
            ),
          // ── Document list ──────────────────────────────────────────────────
          SliverPadding(
            padding: EdgeInsets.fromLTRB(
              AppSpacing.lg,
              hasAnyTags ? AppSpacing.sm : AppSpacing.md,
              AppSpacing.lg,
              AppSpacing.xxxl,
            ),
            sliver: SliverList.separated(
              itemCount: filtered.length,
              separatorBuilder: (context, index) =>
                  const SizedBox(height: AppSpacing.sm),
              itemBuilder: (context, i) {
                final meta = filtered[i];
                return _DocCard(
                  meta: meta,
                  kind: widget.kind,
                  deleting: state.deletingId == meta.id,
                  onTap: () => widget.onOpen(meta),
                  onDelete: () => _confirmDelete(meta),
                  onLongPress: () => _editTags(meta),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  IconData get _emptyIcon {
    switch (widget.kind) {
      case DocKind.sheets:
        return Icons.table_chart_outlined;
      case DocKind.docs:
        return Icons.description_outlined;
      case DocKind.pdf:
        return Icons.picture_as_pdf_outlined;
    }
  }

  String get _emptyHint {
    switch (widget.kind) {
      case DocKind.sheets:
        return 'Create a blank spreadsheet to get started.';
      case DocKind.docs:
        return 'Create a blank document to get started.';
      case DocKind.pdf:
        return 'Import a PDF from your device.';
    }
  }

  String get _createLabel =>
      widget.kind == DocKind.pdf ? 'Import PDF' : 'New $_singular';

  String get _singular {
    switch (widget.kind) {
      case DocKind.sheets:
        return 'sheet';
      case DocKind.docs:
        return 'doc';
      case DocKind.pdf:
        return 'PDF';
    }
  }
}

// ── Tag filter row ─────────────────────────────────────────────────────────────

class _TagFilterRow extends StatelessWidget {
  const _TagFilterRow({
    required this.tags,
    required this.selected,
    required this.onToggle,
  });

  /// All distinct tags across the current list (sorted).
  final List<String> tags;

  /// Currently selected tags (AND filter).
  final Set<String> selected;

  /// Called with null to clear ("All"), or with a tag name to toggle it.
  final void Function(String? tag) onToggle;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 40,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.lg),
        children: [
          // "All" chip clears the filter.
          LzChip(
            key: const Key('tag-filter-all'),
            label: 'All',
            selected: selected.isEmpty,
            onTap: () => onToggle(null),
          ),
          const SizedBox(width: AppSpacing.sm),
          for (final tag in tags) ...[
            LzChip(
              key: Key('tag-filter-$tag'),
              label: tag,
              selected: selected.contains(tag),
              onTap: () => onToggle(tag),
            ),
            const SizedBox(width: AppSpacing.sm),
          ],
        ],
      ),
    );
  }
}

// ── Card ───────────────────────────────────────────────────────────────────────

class _DocCard extends StatelessWidget {
  const _DocCard({
    required this.meta,
    required this.kind,
    required this.deleting,
    required this.onTap,
    required this.onDelete,
    required this.onLongPress,
  });

  final DocMeta meta;
  final DocKind kind;
  final bool deleting;
  final VoidCallback onTap;
  final VoidCallback onDelete;
  final VoidCallback onLongPress;

  static const int _maxVisibleTags = 3;

  IconData get _icon {
    switch (kind) {
      case DocKind.sheets:
        return Icons.table_chart_rounded;
      case DocKind.docs:
        return Icons.description_rounded;
      case DocKind.pdf:
        return Icons.picture_as_pdf_rounded;
    }
  }

  String get _subtitle {
    final parts = <String>[];
    if (kind == DocKind.pdf && meta.pages != null) {
      parts.add('${meta.pages} ${meta.pages == 1 ? "page" : "pages"}');
    }
    final when = _relativeDate(meta.updatedAt ?? meta.createdAt);
    if (when != null) parts.add(when);
    return parts.join(' · ');
  }

  @override
  Widget build(BuildContext context) {
    final visibleTags = meta.tags.take(_maxVisibleTags).toList();
    final overflow = meta.tags.length - visibleTags.length;
    final hasTags = meta.tags.isNotEmpty;

    // LzCard doesn't expose onLongPress, so we layer a GestureDetector for the
    // long-press and pass the tap through to LzCard normally.
    final card = LzCard(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.sm,
      ),
      onTap: deleting ? null : onTap,
      child: Row(
        children: [
          Container(
            width: 38,
            height: 38,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: AppColors.accent.withValues(alpha: 0.12),
              borderRadius: AppRadii.rMd,
            ),
            child: Icon(_icon, size: 20, color: AppColors.accent),
          ),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  meta.name,
                  style: AppText.label.copyWith(color: AppColors.textPrimary),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                if (_subtitle.isNotEmpty) ...[
                  const SizedBox(height: 2),
                  Text(_subtitle, style: AppText.caption),
                ],
                if (hasTags) ...[
                  const SizedBox(height: AppSpacing.xs),
                  Wrap(
                    spacing: AppSpacing.xs,
                    runSpacing: AppSpacing.xs,
                    children: [
                      for (final tag in visibleTags)
                        _TagChip(tag: tag),
                      if (overflow > 0)
                        _TagChip(tag: '+$overflow', muted: true),
                    ],
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          if (deleting)
            const SizedBox(
              width: 40,
              height: 40,
              child: Center(
                child: SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: AppColors.error,
                  ),
                ),
              ),
            )
          else
            LzIconButton(
              icon: Icons.delete_outline_rounded,
              tooltip: 'Delete',
              color: AppColors.textMuted,
              onPressed: onDelete,
            ),
        ],
      ),
    );

    if (deleting) return card;

    return GestureDetector(
      onLongPress: onLongPress,
      child: card,
    );
  }
}

// ── Tag chip (display-only, accent-tinted) ────────────────────────────────────

/// Small display chip for a tag on a document card (up to 3 + "+N" overflow).
class _TagChip extends StatelessWidget {
  const _TagChip({required this.tag, this.muted = false});

  final String tag;

  /// When true, uses [AppColors.textMuted] tint (used for "+N" overflow chip).
  final bool muted;

  @override
  Widget build(BuildContext context) {
    final color = muted ? AppColors.textMuted : AppColors.accent;
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.sm,
        vertical: 2,
      ),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: AppRadii.rPill,
        border: Border.all(color: color.withValues(alpha: 0.25)),
      ),
      child: Text(
        tag,
        style: AppText.caption.copyWith(
          color: color,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

// ── Tag editor bottom-sheet ────────────────────────────────────────────────────

class _TagEditorSheet extends StatefulWidget {
  const _TagEditorSheet({
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
      builder: (ctx) => _TagEditorSheet(
        current: current,
        knownTags: knownTags,
      ),
    );
  }

  @override
  State<_TagEditorSheet> createState() => _TagEditorSheetState();
}

class _TagEditorSheetState extends State<_TagEditorSheet> {
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

/// Compact relative-time for an ISO/`YYYY-MM-DD HH:MM:SS` timestamp
/// ("just now", "5m ago", "3d ago"). Returns null when unparseable.
String? _relativeDate(String? raw) {
  if (raw == null || raw.isEmpty) return null;
  // Server timestamps are UTC ("YYYY-MM-DD HH:MM:SS"); normalize to ISO-UTC.
  final iso = raw.contains('T') ? raw : raw.replaceFirst(' ', 'T');
  final then = DateTime.tryParse(iso.endsWith('Z') ? iso : '${iso}Z');
  if (then == null) return null;
  final diff = DateTime.now().difference(then);
  if (diff.inSeconds < 60) return 'just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
  if (diff.inHours < 24) return '${diff.inHours}h ago';
  if (diff.inDays < 30) return '${diff.inDays}d ago';
  final months = (diff.inDays / 30).floor();
  if (months < 12) return '${months}mo ago';
  return '${(diff.inDays / 365).floor()}y ago';
}
