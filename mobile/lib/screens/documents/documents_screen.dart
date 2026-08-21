import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../providers/documents_provider.dart';
import '../../repositories/documents_repository.dart';
import 'doc_editor_screen.dart';
import 'documents_list_view.dart';
import 'pdf_viewer_screen.dart';
import 'sheet_editor_screen.dart';

/// The Documents tab — mobile access to the user's encrypted office suite.
///
/// Three sub-tabs (Sheets · Docs · PDF), each a [DocumentsListView]. A single
/// kind-aware action creates a blank sheet/doc (name prompt) or imports a PDF
/// (file picker). Offline-first: sheets/docs are created locally (a dirty cache
/// row + an outbox `create`) and read through the on-device cache, while PDFs
/// are import-only and fetched over the wire.
class DocumentsScreen extends ConsumerStatefulWidget {
  const DocumentsScreen({super.key});

  @override
  ConsumerState<DocumentsScreen> createState() => _DocumentsScreenState();
}

class _DocumentsScreenState extends ConsumerState<DocumentsScreen> {
  DocKind _kind = DocKind.sheets;

  static const _kinds = [DocKind.sheets, DocKind.docs, DocKind.pdf];

  /// Open [meta] in the editor for [kind].
  ///
  /// [kind] is REQUIRED rather than read off the mutable `_kind` field: create
  /// and import both await a dialog or the OS file picker, and the user can
  /// switch tabs while that is open. Reading `_kind` afterwards handed a sheet
  /// id to the doc editor (or vice versa) — the wrong parser, hence an empty
  /// document on a near-black screen. Passing the kind captured at the start of
  /// the flow makes that mismatch impossible.
  void _open(DocKind kind, DocMeta meta) {
    final Widget screen;
    switch (kind) {
      case DocKind.sheets:
        screen = SheetEditorScreen(id: meta.id, name: meta.name);
      case DocKind.docs:
        screen = DocEditorScreen(id: meta.id, name: meta.name);
      case DocKind.pdf:
        screen = PdfViewerScreen(id: meta.id, name: meta.name);
    }
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => screen));
  }

  Future<void> _create() async {
    // Capture the kind ONCE, up front — everything below awaits.
    final kind = _kind;
    if (kind == DocKind.pdf) {
      await _import(kind);
    } else {
      await _createBlank(kind);
    }
  }

  Future<void> _createBlank(DocKind kind) async {
    final name = await _promptName(kind);
    if (name == null || !mounted) return;
    final meta =
        await ref.read(documentsListProvider(kind).notifier).createBlank(name);
    if (meta != null && mounted) _open(kind, meta);
  }

  /// The upload extension for each kind's import.
  static String _importExt(DocKind kind) => switch (kind) {
        DocKind.sheets => 'xlsx',
        DocKind.docs => 'docx',
        DocKind.pdf => 'pdf',
      };

  Future<void> _import(DocKind kind) async {
    final FilePickerResult? result;
    try {
      result = await FilePicker.pickFiles(
        type: FileType.custom,
        allowedExtensions: [_importExt(kind)],
        withData: false,
      );
    } catch (_) {
      if (mounted) _snack('Could not open the file picker.', error: true);
      return;
    }
    final path = result?.files.singleOrNull?.path;
    if (path == null || !mounted) return;
    final meta =
        await ref.read(documentsListProvider(kind).notifier).import(File(path));
    if (meta != null && mounted) {
      _open(kind, meta);
    } else if (mounted) {
      _snack('Could not import that .${_importExt(kind)} file.', error: true);
    }
  }

  /// Ask for a name for a new sheet/doc. Null when cancelled.
  ///
  /// The controller is owned by [_NameField], NOT by this method. Creating it
  /// here and disposing it after the `await` looks right and is not: the
  /// dialog's future completes the instant `Navigator.pop` runs, but its widget
  /// tree stays mounted for the whole exit transition — so the dispose lands on
  /// a still-live `TextField` and the next frame throws "A TextEditingController
  /// was used after being disposed", taking the route down with it. Letting the
  /// field's own State own the lifecycle ties disposal to the widget actually
  /// leaving the tree.
  Future<String?> _promptName(DocKind kind) {
    final isSheet = kind == DocKind.sheets;
    final fieldKey = GlobalKey<_NameFieldState>();

    return LzDialog.show<String>(
      context,
      title: 'New ${isSheet ? "sheet" : "doc"}',
      content: _NameField(
        key: fieldKey,
        initial: isSheet ? 'Untitled sheet' : 'Untitled doc',
        // Keyboard "done" — pop through the dialog's OWN context.
        onSubmit: (name) => _popWithName(fieldKey.currentContext, name),
      ),
      // actionsBuilder hands us the DIALOG's context. Popping with the outer
      // screen context would dismiss the wrong route (see the confirm-dialog
      // freeze in mobile/CLAUDE.md).
      actionsBuilder: (dialogContext) => [
        LzButton.ghost(
          label: 'Cancel',
          onPressed: () => Navigator.of(dialogContext).pop(),
        ),
        const SizedBox(width: AppSpacing.sm),
        LzButton.primary(
          label: 'Create',
          icon: Icons.add_rounded,
          onPressed: () =>
              _popWithName(dialogContext, fieldKey.currentState?.value ?? ''),
        ),
      ],
    );
  }

  /// Close the name dialog with [name] (null when it's blank).
  void _popWithName(BuildContext? dialogContext, String name) {
    if (dialogContext == null || !dialogContext.mounted) return;
    final trimmed = name.trim();
    Navigator.of(dialogContext).pop(trimmed.isEmpty ? null : trimmed);
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
        title: 'Documents',
        large: true,
        gradientTitle: true,
        actions: [
          // Sheets/Docs import lives here (PDF import is the FAB). xlsx → sheet,
          // docx → doc.
          if (_kind != DocKind.pdf)
            LzIconButton(
              icon: Icons.file_upload_outlined,
              tooltip: 'Import .${_importExt(_kind)}',
              onPressed: () => _import(_kind),
            ),
        ],
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
                    onOpen: (meta) => _open(k, meta),
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

/// The name input inside the "New sheet/doc" dialog.
///
/// A [StatefulWidget] purely so the [TextEditingController]'s lifetime is tied
/// to the widget's own presence in the tree. A controller created by the caller
/// and disposed after `showDialog`'s future completes is disposed too early —
/// the dialog stays mounted through its exit animation — and the resulting
/// "used after being disposed" assertion kills the whole route.
class _NameField extends StatefulWidget {
  const _NameField({super.key, required this.initial, required this.onSubmit});

  final String initial;

  /// Called with the entered text when the field is submitted.
  final void Function(String name) onSubmit;

  @override
  State<_NameField> createState() => _NameFieldState();
}

class _NameFieldState extends State<_NameField> {
  late final TextEditingController _ctrl =
      TextEditingController(text: widget.initial);

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  /// The text currently typed — read by the dialog's Create button.
  String get value => _ctrl.text;

  @override
  Widget build(BuildContext context) {
    return LzTextField(
      controller: _ctrl,
      label: 'Name',
      autofocus: true,
      textInputAction: TextInputAction.done,
      onSubmitted: (_) => widget.onSubmit(_ctrl.text),
    );
  }
}
