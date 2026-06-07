import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../providers/documents_provider.dart';
import '../../repositories/documents_repository.dart';
import 'doc_viewer_screen.dart';
import 'documents_list_view.dart';
import 'pdf_viewer_screen.dart';
import 'sheet_editor_screen.dart';

/// The Documents tab — mobile access to the user's encrypted office suite.
///
/// Three sub-tabs (Sheets · Docs · PDF), each a [DocumentsListView]. A single
/// kind-aware action creates a blank sheet/doc (name prompt) or imports a PDF
/// (file picker). Online-only — no offline cache (the suite is decrypted
/// server-side per request, like Chat + the power surfaces).
class DocumentsScreen extends ConsumerStatefulWidget {
  const DocumentsScreen({super.key});

  @override
  ConsumerState<DocumentsScreen> createState() => _DocumentsScreenState();
}

class _DocumentsScreenState extends ConsumerState<DocumentsScreen> {
  DocKind _kind = DocKind.sheets;

  static const _kinds = [DocKind.sheets, DocKind.docs, DocKind.pdf];

  void _open(DocMeta meta) {
    final Widget screen;
    switch (_kind) {
      case DocKind.sheets:
        screen = SheetEditorScreen(id: meta.id, name: meta.name);
      case DocKind.docs:
        screen = DocViewerScreen(id: meta.id, name: meta.name);
      case DocKind.pdf:
        screen = PdfViewerScreen(id: meta.id, name: meta.name);
    }
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => screen));
  }

  Future<void> _create() async {
    if (_kind == DocKind.pdf) {
      await _importPdf();
    } else {
      await _createBlank(_kind);
    }
  }

  Future<void> _createBlank(DocKind kind) async {
    final name = await _promptName(kind);
    if (name == null || !mounted) return;
    final meta =
        await ref.read(documentsListProvider(kind).notifier).createBlank(name);
    if (meta != null && mounted) _open(meta);
  }

  Future<void> _importPdf() async {
    final FilePickerResult? result;
    try {
      result = await FilePicker.pickFiles(
        type: FileType.custom,
        allowedExtensions: ['pdf'],
        withData: false,
      );
    } catch (_) {
      if (mounted) _snack('Could not open the file picker.', error: true);
      return;
    }
    final path = result?.files.singleOrNull?.path;
    if (path == null || !mounted) return;
    final meta = await ref
        .read(documentsListProvider(DocKind.pdf).notifier)
        .importPdf(File(path));
    if (meta != null && mounted) _open(meta);
  }

  Future<String?> _promptName(DocKind kind) {
    final ctrl = TextEditingController(
      text: kind == DocKind.sheets ? 'Untitled sheet' : 'Untitled doc',
    );
    return LzDialog.show<String>(
      context,
      title: 'New ${kind == DocKind.sheets ? "sheet" : "doc"}',
      content: LzTextField(
        controller: ctrl,
        label: 'Name',
        autofocus: true,
        textInputAction: TextInputAction.done,
        onSubmitted: (_) =>
            Navigator.of(context).pop(ctrl.text.trim().isEmpty ? null : ctrl.text.trim()),
      ),
      actions: [
        LzButton.ghost(
          label: 'Cancel',
          onPressed: () => Navigator.of(context).pop(),
        ),
        const SizedBox(width: AppSpacing.sm),
        LzButton.primary(
          label: 'Create',
          icon: Icons.add_rounded,
          onPressed: () => Navigator.of(context)
              .pop(ctrl.text.trim().isEmpty ? null : ctrl.text.trim()),
        ),
      ],
    );
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
      appBar: const LzAppBar(
        title: 'Documents',
        large: true,
        gradientTitle: true,
      ),
      floatingActionButton: FloatingActionButton(
        backgroundColor: AppColors.accent,
        foregroundColor: AppColors.onAccent,
        tooltip: _kind == DocKind.pdf ? 'Import PDF' : 'New ${_kind.label}',
        onPressed: _create,
        child: Icon(
          _kind == DocKind.pdf ? Icons.upload_file_rounded : Icons.add,
        ),
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.lg,
              AppSpacing.md,
              AppSpacing.lg,
              AppSpacing.sm,
            ),
            child: Row(
              children: [
                for (final k in _kinds) ...[
                  LzChip(
                    label: k.label,
                    icon: _tabIcon(k),
                    selected: _kind == k,
                    onTap: () {
                      if (k != _kind) setState(() => _kind = k);
                    },
                  ),
                  const SizedBox(width: AppSpacing.sm),
                ],
              ],
            ),
          ),
          Expanded(
            child: IndexedStack(
              index: _kinds.indexOf(_kind),
              children: [
                for (final k in _kinds)
                  DocumentsListView(
                    key: ValueKey(k),
                    kind: k,
                    onOpen: _open,
                    onCreate: _create,
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  IconData _tabIcon(DocKind k) {
    switch (k) {
      case DocKind.sheets:
        return Icons.table_chart_outlined;
      case DocKind.docs:
        return Icons.description_outlined;
      case DocKind.pdf:
        return Icons.picture_as_pdf_outlined;
    }
  }
}
