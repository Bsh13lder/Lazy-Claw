import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../providers/documents_provider.dart';
import '../../repositories/documents_repository.dart';
import 'doc_ai_box.dart';
import 'univer_parse.dart';

/// Read-only viewer for a single document, plus the ✨ AI-edit box.
///
/// Renders the Univer `IDocumentData` `body.dataStream` as plain-text
/// paragraphs. Manual rich-text editing is out of scope for this pass — edits
/// go through the AI box (which returns a fresh snapshot we re-render).
class DocViewerScreen extends ConsumerStatefulWidget {
  const DocViewerScreen({super.key, required this.id, required this.name});

  final String id;
  final String name;

  @override
  ConsumerState<DocViewerScreen> createState() => _DocViewerScreenState();
}

class _DocViewerScreenState extends ConsumerState<DocViewerScreen> {
  bool _loading = true;
  bool _applying = false;
  String? _error;
  List<String> _paragraphs = const [];

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final repo = ref.read(documentsRepositoryProvider);
      final detail = await repo.getPayload(DocKind.docs, widget.id);
      if (!mounted) return;
      setState(() {
        _paragraphs = parseDocParagraphs(detail.payload);
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = 'Could not open this document. Pull to retry.';
        _loading = false;
      });
    }
  }

  Future<void> _openAi() async {
    final instruction = await DocAiBox.show(context, kindLabel: 'document');
    if (instruction == null || !mounted) return;
    setState(() => _applying = true);
    try {
      final repo = ref.read(documentsRepositoryProvider);
      final result = await repo.aiEdit(DocKind.docs, widget.id, instruction);
      if (!mounted) return;
      setState(() => _applying = false);
      if (result.ok && result.snapshot != null) {
        setState(() => _paragraphs = parseDocParagraphs(result.snapshot));
        ref.read(documentsListProvider(DocKind.docs).notifier).refresh();
        _snack(result.summary ?? 'Document updated.');
      } else {
        _snack(result.error ?? 'AI could not apply that change.', error: true);
      }
    } catch (_) {
      if (!mounted) return;
      setState(() => _applying = false);
      _snack('AI edit failed. Try again.', error: true);
    }
  }

  void _snack(String msg, {bool error = false}) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        backgroundColor: AppColors.bgSurfaceElevated,
        content: Text(
          msg,
          style: AppText.body.copyWith(
            color: error ? AppColors.error : AppColors.textPrimary,
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return LzScaffold(
      appBar: LzAppBar(
        title: widget.name,
        actions: [
          LzIconButton(
            icon: Icons.auto_awesome,
            tooltip: 'Ask AI to edit',
            accent: true,
            onPressed: _applying ? null : _openAi,
          ),
        ],
      ),
      body: Stack(
        children: [
          _buildBody(),
          if (_applying) const AiApplyingOverlay(),
        ],
      ),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return LzSkeleton.list(count: 5);
    }
    if (_error != null) {
      return LzErrorState(message: _error!, onRetry: _load);
    }
    final hasText = _paragraphs.any((p) => p.trim().isNotEmpty);
    if (!hasText) {
      return const LzEmptyState(
        icon: Icons.article_outlined,
        title: 'Empty document',
        hint: 'This document has no text yet. Use ✨ to add some.',
      );
    }
    return LzRefresh(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.lg,
          AppSpacing.lg,
          AppSpacing.lg,
          AppSpacing.xxxl,
        ),
        children: [
          LzCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (final p in _paragraphs) ...[
                  SelectableText(
                    p.isEmpty ? ' ' : p,
                    style: AppText.bodyL.copyWith(height: 1.5),
                  ),
                  const SizedBox(height: AppSpacing.md),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}
