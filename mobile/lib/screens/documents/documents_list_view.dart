import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../providers/documents_provider.dart';
import '../../repositories/documents_repository.dart';

/// The list for one [DocKind] sub-tab: skeleton / error / empty / cards.
///
/// Network-only (the office suite is decrypted server-side per request), so this
/// shows loading + error states from the kit. Tapping a card calls [onOpen];
/// the empty-state action calls [onCreate]; per-card delete confirms first.
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

    return LzRefresh(
      onRefresh: notifier.refresh,
      child: ListView.separated(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.lg,
          AppSpacing.md,
          AppSpacing.lg,
          AppSpacing.xxxl,
        ),
        itemCount: state.items.length,
        separatorBuilder: (context, index) =>
            const SizedBox(height: AppSpacing.sm),
        itemBuilder: (context, i) {
          final meta = state.items[i];
          return _DocCard(
            meta: meta,
            kind: widget.kind,
            deleting: state.deletingId == meta.id,
            onTap: () => widget.onOpen(meta),
            onDelete: () => _confirmDelete(meta),
          );
        },
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

// ── Card ─────────────────────────────────────────────────────────────────────

class _DocCard extends StatelessWidget {
  const _DocCard({
    required this.meta,
    required this.kind,
    required this.deleting,
    required this.onTap,
    required this.onDelete,
  });

  final DocMeta meta;
  final DocKind kind;
  final bool deleting;
  final VoidCallback onTap;
  final VoidCallback onDelete;

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
    return LzCard(
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
