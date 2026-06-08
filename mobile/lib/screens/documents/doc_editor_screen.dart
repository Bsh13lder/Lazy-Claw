import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_quill/flutter_quill.dart';
import 'package:flutter_quill/quill_delta.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../providers/documents_provider.dart';
import '../../repositories/documents_repository.dart';
import 'doc_ai_box.dart';
import 'doc_share.dart';
import 'univer_quill.dart';

/// Full native rich-text editor for a single document.
///
/// Loads the Univer `IDocumentData` snapshot, converts it to a Quill document
/// ([deltaFromUniver]), and edits it with a real toolbar (bold/italic/underline,
/// H1–H3, bullet & numbered lists, links). On change it converts back
/// ([univerFromDelta]) and autosaves. The ✨ AI box still applies one-shot edits.
class DocEditorScreen extends ConsumerStatefulWidget {
  const DocEditorScreen({super.key, required this.id, required this.name});

  final String id;
  final String name;

  @override
  ConsumerState<DocEditorScreen> createState() => _DocEditorScreenState();
}

class _DocEditorScreenState extends ConsumerState<DocEditorScreen> {
  bool _loading = true;
  bool _applying = false;
  bool _saving = false;
  String? _error;

  QuillController? _controller;
  Map<String, dynamic> _basePayload = const {};
  final FocusNode _editorFocus = FocusNode();
  StreamSubscription<dynamic>? _changeSub;
  Timer? _saveTimer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  @override
  void dispose() {
    _saveTimer?.cancel();
    _changeSub?.cancel();
    _controller?.dispose();
    _editorFocus.dispose();
    super.dispose();
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
      _basePayload = detail.payload;
      _installController(deltaFromUniver(detail.payload));
      setState(() => _loading = false);
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = 'Could not open this document. Pull to retry.';
        _loading = false;
      });
    }
  }

  void _installController(Delta delta) {
    _changeSub?.cancel();
    _controller?.dispose();
    final doc = delta.isEmpty ? Document() : Document.fromDelta(delta);
    final controller = QuillController(
      document: doc,
      selection: const TextSelection.collapsed(offset: 0),
    );
    // Only content changes (not selection moves) schedule an autosave.
    _changeSub = controller.document.changes.listen((_) => _scheduleSave());
    _controller = controller;
  }

  void _scheduleSave() {
    _saveTimer?.cancel();
    _saveTimer = Timer(const Duration(milliseconds: 800), _save);
  }

  Future<void> _save() async {
    final controller = _controller;
    if (controller == null) return;
    setState(() => _saving = true);
    try {
      final body = univerFromDelta(controller.document.toDelta())['body'];
      // Preserve the doc's original top-level keys (id/documentStyle), swap body.
      final payload = {..._basePayload, 'body': body};
      await ref
          .read(documentsRepositoryProvider)
          .save(DocKind.docs, widget.id, widget.name, payload);
      ref.read(documentsListProvider(DocKind.docs).notifier).refresh();
    } catch (_) {
      // Best-effort autosave; the next edit reschedules.
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  static const _docxMime =
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

  Future<void> _export(String format, String ext, String mime) async {
    setState(() => _saving = true);
    try {
      final bytes = await ref
          .read(documentsRepositoryProvider)
          .exportBytes(DocKind.docs, widget.id, format);
      await shareDocumentBytes(
        bytes: bytes, stem: widget.name, ext: ext, mimeType: mime,
      );
    } catch (_) {
      if (mounted) {
        _snack('Export failed (PDF needs LibreOffice on the server).',
            error: true);
      }
    } finally {
      if (mounted) setState(() => _saving = false);
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
      setState(() {
        _applying = false;
        if (result.ok && result.snapshot != null) {
          _basePayload = result.snapshot!;
          _installController(deltaFromUniver(result.snapshot));
        }
      });
      if (result.ok && result.snapshot != null) {
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
          if (_saving)
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: AppSpacing.md),
              child: Center(
                child: SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: AppColors.accent,
                  ),
                ),
              ),
            ),
          PopupMenuButton<String>(
            icon: const Icon(Icons.ios_share, color: AppColors.textSecondary),
            tooltip: 'Export / share',
            color: AppColors.bgSurfaceElevated,
            onSelected: (v) {
              if (v == 'docx') {
                _export('docx', 'docx', _docxMime);
              } else if (v == 'pdf') {
                _export('pdf', 'pdf', 'application/pdf');
              }
            },
            itemBuilder: (_) => const [
              PopupMenuItem(value: 'docx', child: Text('Export as Word (.docx)')),
              PopupMenuItem(value: 'pdf', child: Text('Export as PDF (.pdf)')),
            ],
          ),
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
    if (_loading) return LzSkeleton.list(count: 5);
    if (_error != null) return LzErrorState(message: _error!, onRetry: _load);
    final controller = _controller;
    if (controller == null) return const SizedBox.shrink();
    return Column(
      children: [
        QuillSimpleToolbar(
          controller: controller,
          config: const QuillSimpleToolbarConfig(
            showFontFamily: false,
            showFontSize: false,
            showColorButton: false,
            showBackgroundColorButton: false,
            showClearFormat: true,
            showCodeBlock: false,
            showInlineCode: false,
            showQuote: false,
            showSubscript: false,
            showSuperscript: false,
            showSearchButton: false,
            showStrikeThrough: false,
            showIndent: false,
            multiRowsDisplay: false,
          ),
        ),
        const Divider(height: 1, color: AppColors.borderSubtle),
        Expanded(
          child: Container(
            color: AppColors.bgSurface,
            padding: const EdgeInsets.all(AppSpacing.lg),
            child: QuillEditor.basic(
              controller: controller,
              focusNode: _editorFocus,
              config: const QuillEditorConfig(
                placeholder: 'Start writing… use the toolbar for headings & lists',
              ),
            ),
          ),
        ),
      ],
    );
  }
}
