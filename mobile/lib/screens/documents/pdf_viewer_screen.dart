import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:pdfx/pdfx.dart';

import '../../providers/documents_provider.dart';
import '../../repositories/documents_repository.dart';
import 'doc_ai_box.dart';
import 'doc_share.dart';
import 'export_password_dialog.dart';

/// PDF viewer (pdfx — MIT, pdfium-backed) fed by `GET /api/pdf/{id}/raw`, plus
/// the ✨ AI-edit box.
///
/// PDF ops are immutable server-side: a successful AI edit returns a NEW
/// `new_pdf_id`, so we swap the viewer to it in place (and the old file stays
/// untouched). PDFs can't be reflow text-edited — the AI box drives
/// sign/fill/merge/split/rotate/generate.
class PdfViewerScreen extends ConsumerStatefulWidget {
  const PdfViewerScreen({super.key, required this.id, required this.name});

  final String id;
  final String name;

  @override
  ConsumerState<PdfViewerScreen> createState() => _PdfViewerScreenState();
}

class _PdfViewerScreenState extends ConsumerState<PdfViewerScreen> {
  late String _pdfId = widget.id;
  bool _loading = true;
  bool _applying = false;
  String? _error;
  PdfControllerPinch? _controller;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  @override
  void dispose() {
    _controller?.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final cache = ref.read(documentCacheDaoProvider);
    // PDFs are immutable per id (an edit mints a NEW id), so a cached copy is
    // always fresh — render it and skip the network entirely (also = offline).
    if (cache != null) {
      final cached = await cache.getDoc(DocKind.pdf.api, _pdfId);
      final cbytes = cached?.bytes;
      if (cbytes != null && cbytes.isNotEmpty) {
        if (!mounted) return;
        _renderBytes(cbytes);
        setState(() {
          _loading = false;
          _error = null;
        });
        return;
      }
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final repo = ref.read(documentsRepositoryProvider);
      final bytes = await repo.getPdfBytes(_pdfId);
      if (!mounted) return;
      _renderBytes(bytes);
      setState(() => _loading = false);
      try {
        await cache?.putDoc(
          kind: DocKind.pdf.api,
          id: _pdfId,
          name: widget.name,
          bytes: bytes,
        );
      } catch (_) {
        // Caching is best-effort.
      }
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = 'Could not open this PDF. Pull to retry.';
        _loading = false;
      });
    }
  }

  void _renderBytes(List<int> bytes) {
    final doc = PdfDocument.openData(Uint8List.fromList(bytes));
    if (_controller == null) {
      _controller = PdfControllerPinch(document: doc);
    } else {
      _controller!.loadDocument(doc);
    }
  }

  Future<void> _openAi() async {
    final instruction = await DocAiBox.show(context, kindLabel: 'PDF');
    if (instruction == null || !mounted) return;
    setState(() => _applying = true);
    try {
      final repo = ref.read(documentsRepositoryProvider);
      final result = await repo.aiEdit(DocKind.pdf, _pdfId, instruction);
      if (!mounted) return;
      setState(() => _applying = false);
      if (result.ok && result.newPdfId != null) {
        // Immutable op: switch to the freshly produced file.
        _pdfId = result.newPdfId!;
        ref.read(documentsListProvider(DocKind.pdf).notifier).refresh();
        _snack(result.summary ?? 'PDF updated.');
        await _load();
      } else {
        _snack(result.error ?? 'AI could not apply that change.', error: true);
      }
    } catch (_) {
      if (!mounted) return;
      setState(() => _applying = false);
      _snack('AI edit failed. Try again.', error: true);
    }
  }

  Future<void> _downloadShare() async {
    final pw = await promptExportPassword(context);
    if (pw == null || !mounted) return;
    final encrypted = pw.isNotEmpty;
    setState(() => _applying = true);
    try {
      final bytes = await ref
          .read(documentsRepositoryProvider)
          .downloadPdf(_pdfId, password: encrypted ? pw : null);
      await shareDocumentBytes(
        bytes: bytes,
        stem: widget.name,
        ext: encrypted ? 'zip' : 'pdf',
        mimeType: encrypted ? 'application/zip' : 'application/pdf',
      );
    } catch (_) {
      if (mounted) _snack('Download failed. Try again.', error: true);
    } finally {
      if (mounted) setState(() => _applying = false);
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
            icon: Icons.ios_share,
            tooltip: 'Download / share',
            onPressed: _applying ? null : _downloadShare,
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
    if (_loading && _controller == null) {
      return LzSkeleton.list(count: 4);
    }
    if (_error != null) {
      return LzErrorState(message: _error!, onRetry: _load);
    }
    final controller = _controller;
    if (controller == null) {
      return const LzEmptyState(
        icon: Icons.picture_as_pdf_outlined,
        title: 'Nothing to show',
        hint: 'This PDF could not be rendered.',
      );
    }
    return ColoredBox(
      color: AppColors.bgBase,
      child: PdfViewPinch(controller: controller),
    );
  }
}
