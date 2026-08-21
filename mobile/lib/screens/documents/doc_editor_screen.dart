import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_quill/flutter_quill.dart';
import 'package:flutter_quill/quill_delta.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../local/document_cache_dao.dart';
import '../../providers/documents_provider.dart';
import '../../repositories/documents_repository.dart';
import 'doc_ai_box.dart';
import 'doc_share.dart';
import 'export_password_dialog.dart';
import 'sheet_conflict_banner.dart';
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

class _DocEditorScreenState extends ConsumerState<DocEditorScreen>
    with WidgetsBindingObserver {
  bool _loading = true;
  bool _applying = false;
  bool _saving = false;
  String? _error;

  QuillController? _controller;
  Map<String, dynamic> _basePayload = const {};
  final FocusNode _editorFocus = FocusNode();
  StreamSubscription<dynamic>? _changeSub;
  Timer? _saveTimer;

  // ── Optimistic-concurrency tracking ────────────────────────────────────────
  /// The `updated_at` value from the server when we last loaded or saved.
  String? _baseUpdatedAt;

  /// Non-null when a save returned a 409 DocConflictException.
  DocPayload? _conflict;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _saveTimer?.cancel();
    _changeSub?.cancel();
    _controller?.dispose();
    _editorFocus.dispose();
    super.dispose();
  }

  // ── WidgetsBindingObserver — fresh-on-resume ──────────────────────────────

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _revalidateIfIdle();
    }
  }

  /// Re-run the network half of [_load] when no unsaved edit is in-flight.
  Future<void> _revalidateIfIdle() async {
    if (_saveTimer != null || _saving || _conflict != null) return;
    try {
      final repo = ref.read(documentsRepositoryProvider);
      final detail = await repo.getPayload(DocKind.docs, widget.id);
      // Parse before mutating state — a throw here must leave the document the
      // user is looking at exactly as it was.
      final delta = deltaFromUniver(detail.payload);
      if (!mounted) return;
      _basePayload = detail.payload;
      _baseUpdatedAt = detail.updatedAt;
      _installController(delta);
      setState(() { _loading = false; _error = null; });
      await _cachePayload(detail.payload, detail.name);
    } catch (_) {
      // Revalidation is best-effort.
    }
  }

  /// Read + parse the on-device cached copy, or null when there is nothing
  /// usable. Returns the payload paired with its Quill delta so the caller
  /// installs both together or neither.
  ///
  /// Null on three counts, all of which must fall through to the network: no
  /// cached row; a row carrying no payload (metadata-only — what an import or a
  /// snapshot-less `/changes` pull writes, which decodes to `{}` and would
  /// otherwise paint as a valid empty document); or a read/parse that throws (a
  /// transient SQLite lock — the foreground app and the background sync share
  /// one DB). The parse is INSIDE the try on purpose: `_load` runs from a
  /// post-frame callback, so an escaping throw would leave `_loading` true and
  /// strand the editor on the infinite shimmer skeleton.
  Future<({Map<String, dynamic> payload, Delta delta})?> _readCache(
    DocumentCacheDao? cache,
  ) async {
    if (cache == null) return null;
    try {
      final cached = await cache.getDoc(DocKind.docs.api, widget.id);
      if (cached == null || !cached.hasPayload) return null;
      final payload = cached.payload;
      return (payload: payload, delta: deltaFromUniver(payload));
    } catch (_) {
      return null;
    }
  }

  Future<void> _load() async {
    // 1. Paint the cached copy instantly (no spinner) if we have a usable one.
    final cached = await _readCache(ref.read(documentCacheDaoProvider));
    if (!mounted) return;
    if (cached != null) {
      _basePayload = cached.payload;
      _installController(cached.delta);
      setState(() {
        _loading = false;
        _error = null;
      });
    } else {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    // 2. Revalidate over the network; refresh the view + cache when it lands.
    try {
      final repo = ref.read(documentsRepositoryProvider);
      final detail = await repo.getPayload(DocKind.docs, widget.id);
      // Build the delta BEFORE touching any state, so a parse failure lands in
      // the catch below with `_basePayload` untouched — assigning it first made
      // the catch's `_basePayload.isEmpty` guard permanently false, which is how
      // a throw here used to leave the editor stuck on the skeleton.
      final delta = deltaFromUniver(detail.payload);
      if (!mounted) return;
      _basePayload = detail.payload;
      _baseUpdatedAt = detail.updatedAt;
      _installController(delta);
      setState(() {
        _loading = false;
        _error = null;
      });
      await _cachePayload(detail.payload, detail.name);
    } catch (_) {
      if (!mounted) return;
      // Keep showing the cached copy when offline; only error on a cold miss.
      // ALWAYS clear `_loading` here so a network failure can never strand the
      // editor on the infinite skeleton.
      if (_controller == null) {
        setState(() {
          _error = 'Could not open this document. Pull to retry.';
          _loading = false;
        });
      }
    }
  }

  /// Best-effort write of the current document payload into the on-device cache.
  Future<void> _cachePayload(Map<String, dynamic> payload, String name) async {
    try {
      await ref.read(documentCacheDaoProvider)?.putDoc(
            kind: DocKind.docs.api,
            id: widget.id,
            name: name,
            payloadJson: jsonEncode(payload),
          );
    } catch (_) {
      // Caching is best-effort.
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
    _saveTimer = null;
    final controller = _controller;
    if (controller == null) return;
    setState(() => _saving = true);
    try {
      final body = univerFromDelta(controller.document.toDelta())['body'];
      // Preserve the doc's original top-level keys (id/documentStyle), swap body.
      final payload = {..._basePayload, 'body': body};
      final newUpdatedAt = await ref
          .read(documentsRepositoryProvider)
          .save(DocKind.docs, widget.id, payload,
              // name: null — server keeps its stored name; avoids stale-rename clobber.
              baseUpdatedAt: _baseUpdatedAt);
      _baseUpdatedAt = newUpdatedAt ?? _baseUpdatedAt;
      await _cachePayload(payload, widget.name);
      ref.read(documentsListProvider(DocKind.docs).notifier).refresh();
    } on DocConflictException catch (e) {
      if (mounted) setState(() => _conflict = e.current);
    } catch (e) {
      if (e is Exception) {
        final msg = e.toString();
        if (msg.contains('409')) {
          if (mounted) {
            _snack('Save conflict — reloading.', error: true);
            _load();
          }
          return;
        }
      }
      // Best-effort autosave; the next edit reschedules.
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  // ── Conflict resolution ───────────────────────────────────────────────────────

  Future<void> _resolveConflictReload() async {
    final conflict = _conflict;
    if (conflict == null) return;
    setState(() {
      _conflict = null;
      _basePayload = conflict.payload;
      _baseUpdatedAt = conflict.updatedAt;
    });
    _installController(deltaFromUniver(conflict.payload));
    setState(() {});
    await _cachePayload(conflict.payload, widget.name);
  }

  Future<void> _resolveConflictKeepMine() async {
    final controller = _controller;
    if (controller == null) return;
    setState(() { _conflict = null; _saving = true; });
    try {
      final body = univerFromDelta(controller.document.toDelta())['body'];
      final payload = {..._basePayload, 'body': body};
      // LWW: omit base_updated_at.
      final newUpdatedAt = await ref
          .read(documentsRepositoryProvider)
          .save(DocKind.docs, widget.id, payload);
      _baseUpdatedAt = newUpdatedAt ?? _baseUpdatedAt;
      await _cachePayload(payload, widget.name);
      ref.read(documentsListProvider(DocKind.docs).notifier).refresh();
    } catch (e) {
      if (mounted) _snack('Save failed: $e', error: true);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  static const _docxMime =
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

  Future<void> _export(String format, String ext, String mime) async {
    final pw = await promptExportPassword(context);
    if (pw == null || !mounted) return;
    final encrypted = pw.isNotEmpty;
    setState(() => _saving = true);
    try {
      final bytes = await ref.read(documentsRepositoryProvider).exportBytes(
            DocKind.docs, widget.id, format,
            password: encrypted ? pw : null,
          );
      await shareDocumentBytes(
        bytes: bytes,
        stem: widget.name,
        ext: encrypted ? 'zip' : ext,
        mimeType: encrypted ? 'application/zip' : mime,
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
        await _cachePayload(result.snapshot!, widget.name);
        ref.read(documentsListProvider(DocKind.docs).notifier).refresh();
        _snack(result.summary ?? 'Document updated.');
        // AI edit saves server-side — LWW on next autosave (null base).
        _baseUpdatedAt = null;
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
    // No controller, not loading, no error — previously `SizedBox.shrink()`,
    // i.e. a featureless near-black screen with no message and no way out.
    if (controller == null) {
      return LzErrorState(
        message: 'Could not open this document. Pull to retry.',
        onRetry: _load,
      );
    }
    return Column(
      children: [
        // ── Conflict banner ──────────────────────────────────────────────────
        if (_conflict != null)
          SheetConflictBanner(
            label: 'Document changed on the server.',
            onReload: _resolveConflictReload,
            onKeepMine: _resolveConflictKeepMine,
          ),
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
