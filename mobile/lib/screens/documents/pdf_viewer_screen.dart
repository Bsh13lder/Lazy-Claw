import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:pdfx/pdfx.dart';

import '../../providers/documents_provider.dart';
import '../../repositories/documents_repository.dart';
import 'doc_ai_box.dart';
import 'doc_freshness.dart';
import 'doc_share.dart';
import 'export_password_dialog.dart';

/// PDF viewer (pdfx — MIT, pdfium-backed) fed by `GET /api/pdf/{id}/raw`, plus
/// the ✨ AI-edit box.
///
/// A successful AI edit returns `new_pdf_id`. For single-output ops
/// (sign/fill/rotate/merge) that id EQUALS the open id — the file was edited in
/// place (its prior bytes kept as a recoverable server-side version) — so we
/// force a network reload to replace the now-stale cached bytes. `generate` /
/// `split` return a NEW id and we swap the viewer to it. PDFs can't be reflow
/// text-edited.
class PdfViewerScreen extends ConsumerStatefulWidget {
  const PdfViewerScreen({super.key, required this.id, required this.name});

  final String id;
  final String name;

  @override
  ConsumerState<PdfViewerScreen> createState() => _PdfViewerScreenState();
}

class _PdfViewerScreenState extends ConsumerState<PdfViewerScreen>
    with WidgetsBindingObserver {
  late String _pdfId = widget.id;
  bool _loading = true;
  bool _applying = false;
  String? _error;
  PdfControllerPinch? _controller;
  // updated_at of the bytes currently on screen. When sync learns the server
  // copy is newer (edited on web / another device / an in-place ✨ edit), we
  // auto-reload the fresh bytes in place.
  String? _renderedUpdatedAt;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      await _load();
      // Pull /changes so an edit made elsewhere BEFORE we opened this PDF
      // surfaces; the ref.listen in build() then reloads if it's now stale.
      _triggerRefresh();
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _controller?.dispose();
    super.dispose();
  }

  /// Re-check for a server-side edit whenever the app returns to foreground.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _triggerRefresh();
  }

  /// Drain the outbox + pull /changes for PDFs so the list provider learns of a
  /// server-side edit. Fire-and-forget — offline is a normal no-op, and the
  /// ref.listen in build() reacts to the resulting state change.
  void _triggerRefresh() {
    if (!mounted) return;
    ref.read(documentsListProvider(DocKind.pdf).notifier).refresh();
  }

  /// The freshest server updated_at for the open PDF, from the list provider
  /// (kept current by sync). Null when unknown (list not loaded / offline).
  String? _serverUpdatedAt() {
    for (final m in ref.read(documentsListProvider(DocKind.pdf)).items) {
      if (m.id == _pdfId) return m.updatedAt;
    }
    return null;
  }

  Future<void> _load({bool forceNetwork = false}) async {
    final cache = ref.read(documentCacheDaoProvider);
    final serverUpdatedAt = _serverUpdatedAt();
    // A cached copy is fresh UNLESS the server copy is newer (an in-place ✨
    // edit here, or an edit on web / another device). Serve the cache only when
    // it isn't stale and the caller didn't force the network; otherwise fetch
    // and overwrite the cache with the new bytes + updated_at below.
    if (cache != null && !forceNetwork) {
      // The read AND the render both go inside the try: `_load` runs from a
      // post-frame callback, so a SQLite lock or a corrupt cached blob throwing
      // here would leave `_loading` true forever — the infinite shimmer, which
      // on the dark theme reads as a black screen. Either failure degrades to
      // the network path below.
      try {
        final cached = await cache.getDoc(DocKind.pdf.api, _pdfId);
        final cbytes = cached?.bytes;
        if (cbytes != null &&
            cbytes.isNotEmpty &&
            !isServerNewer(cached?.updatedAt, serverUpdatedAt)) {
          await _renderBytes(cbytes);
          if (!mounted) return;
          _renderedUpdatedAt = cached?.updatedAt ?? serverUpdatedAt;
          setState(() {
            _loading = false;
            _error = null;
          });
          return;
        }
      } catch (_) {
        // Cache miss / unreadable cached bytes — refetch from the server.
      }
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final repo = ref.read(documentsRepositoryProvider);
      final bytes = await repo.getPdfBytes(_pdfId);
      await _renderBytes(bytes);
      if (!mounted) return;
      _renderedUpdatedAt = serverUpdatedAt ?? _renderedUpdatedAt;
      setState(() => _loading = false);
      try {
        // Cache the fresh bytes WITH updated_at so the next staleness check is
        // meaningful (a null updated_at would always look "not newer").
        await cache?.putServerDoc(
          kind: DocKind.pdf.api,
          id: _pdfId,
          name: widget.name,
          bytes: bytes,
          updatedAt: serverUpdatedAt,
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

  /// Open [bytes] with pdfium and hand the document to the view controller.
  ///
  /// AWAITS the open rather than passing the pending future straight to the
  /// controller: a blob pdfium can't parse (a truncated download, an HTML error
  /// page, a server-side generate that produced garbage) used to reject inside
  /// the controller where nothing observed it, leaving `PdfViewPinch` painting
  /// nothing over the near-black background — a silent black screen. Now the
  /// failure propagates to `_load`'s catch and becomes a real message.
  Future<void> _renderBytes(List<int> bytes) async {
    final doc = await PdfDocument.openData(Uint8List.fromList(bytes));
    if (!mounted) {
      await doc.close();
      return;
    }
    if (_controller == null) {
      _controller = PdfControllerPinch(document: Future<PdfDocument>.value(doc));
    } else {
      _controller!.loadDocument(Future<PdfDocument>.value(doc));
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
        // In-place edit → same id (bytes changed); generate/split → new id.
        // Either way the cached copy for the target id is stale. Pull /changes
        // FIRST so the list has this file's new updated_at (keeps the staleness
        // tracker honest), then force a network reload to fetch + re-cache.
        _pdfId = result.newPdfId!;
        await ref.read(documentsListProvider(DocKind.pdf).notifier).refresh();
        if (!mounted) return;
        _snack(result.summary ?? 'PDF updated.');
        await _load(forceNetwork: true);
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
    // Auto-refresh: when sync learns this PDF's server copy is newer (edited on
    // web / another device), reload the fresh bytes in place. Guarded so it
    // never fires mid-load or mid-edit, and only on a genuinely newer stamp.
    ref.listen(documentsListProvider(DocKind.pdf), (_, next) {
      if (_loading || _applying) return;
      String? su;
      for (final m in next.items) {
        if (m.id == _pdfId) {
          su = m.updatedAt;
          break;
        }
      }
      if (isServerNewer(_renderedUpdatedAt, su)) {
        _load(forceNetwork: true);
      }
    });
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
