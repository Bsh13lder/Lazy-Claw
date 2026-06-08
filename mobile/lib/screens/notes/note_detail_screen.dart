import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/note.dart';
import '../../providers/notes_provider.dart';

/// Premium markdown reader + editor for a single note.
///
/// View mode: clean typography via tokens; [flutter_markdown] renders the body.
/// Edit mode: [LzTextField] for raw markdown + a save action in the app bar.
/// All provider calls (updateNote / deleteNote) are unchanged from the
/// original wiring.
class NoteDetailScreen extends ConsumerStatefulWidget {
  const NoteDetailScreen({super.key, required this.note});

  final Note note;

  @override
  ConsumerState<NoteDetailScreen> createState() => _NoteDetailScreenState();
}

class _NoteDetailScreenState extends ConsumerState<NoteDetailScreen> {
  bool _editing = false;
  bool _saving = false;
  late final TextEditingController _titleCtrl;
  late final TextEditingController _contentCtrl;

  @override
  void initState() {
    super.initState();
    _titleCtrl = TextEditingController(text: widget.note.title ?? '');
    _contentCtrl = TextEditingController(text: widget.note.content);
  }

  @override
  void dispose() {
    _titleCtrl.dispose();
    _contentCtrl.dispose();
    super.dispose();
  }

  // ── Actions ───────────────────────────────────────────────────────────────

  Future<void> _save() async {
    final title = _titleCtrl.text.trim();
    final content = _contentCtrl.text;
    setState(() => _saving = true);
    await ref.read(notesProvider.notifier).updateNote(
          widget.note.id,
          title: title.isEmpty ? null : title,
          content: content,
        );
    if (mounted) setState(() { _editing = false; _saving = false; });
  }

  Future<void> _delete() async {
    final confirmed = await LzConfirm.show(
      context,
      title: 'Delete note?',
      message: widget.note.title ??
          (widget.note.contentPreview.isNotEmpty
              ? widget.note.contentPreview
              : 'This note will be permanently removed.'),
      confirmLabel: 'Delete',
      cancelLabel: 'Cancel',
      danger: true,
    );
    if (!confirmed || !mounted) return;
    await ref.read(notesProvider.notifier).deleteNote(widget.note.id);
    if (mounted) Navigator.of(context).pop();
  }

  // ── Build ─────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return LzScaffold(
      resizeToAvoidBottomInset: true,
      appBar: _NoteAppBar(
        note: widget.note,
        editing: _editing,
        saving: _saving,
        titleCtrl: _titleCtrl,
        onEdit: () => setState(() => _editing = true),
        onSave: _save,
        onDelete: _delete,
      ),
      body: AnimatedSwitcher(
        duration: AppMotion.base,
        switchInCurve: AppMotion.curve,
        switchOutCurve: AppMotion.curve,
        child: _editing
            ? _EditView(
                key: const ValueKey('edit'),
                contentCtrl: _contentCtrl,
              )
            : _ReadView(
                key: const ValueKey('read'),
                note: widget.note,
              ),
      ),
    );
  }
}

// ── Custom app bar for the detail screen ─────────────────────────────────────

/// A [PreferredSizeWidget] that shows either the note title (string) or an
/// inline [TextField] (edit mode) as the bar title, so [LzScaffold] can accept
/// it via its custom [appBar] slot.
class _NoteAppBar extends StatelessWidget implements PreferredSizeWidget {
  const _NoteAppBar({
    required this.note,
    required this.editing,
    required this.saving,
    required this.titleCtrl,
    required this.onEdit,
    required this.onSave,
    required this.onDelete,
  });

  final Note note;
  final bool editing;
  final bool saving;
  final TextEditingController titleCtrl;
  final VoidCallback onEdit;
  final VoidCallback onSave;
  final VoidCallback onDelete;

  @override
  Size get preferredSize => const Size.fromHeight(kToolbarHeight);

  @override
  Widget build(BuildContext context) {
    final titleWidget = editing
        ? TextField(
            controller: titleCtrl,
            style: AppText.titleL,
            cursorColor: AppColors.accent,
            textInputAction: TextInputAction.next,
            decoration: InputDecoration(
              hintText: 'Title (optional)',
              hintStyle: AppText.titleL.copyWith(color: AppColors.textMuted),
              border: InputBorder.none,
              isDense: true,
              contentPadding: EdgeInsets.zero,
            ),
          )
        : Text(
            note.title ?? 'Untitled',
            style: AppText.titleL,
            overflow: TextOverflow.ellipsis,
          );

    final actions = <Widget>[];
    if (saving) {
      actions.add(const Padding(
        padding: EdgeInsets.all(AppSpacing.md),
        child: SizedBox(
          width: 20,
          height: 20,
          child: CircularProgressIndicator(
            strokeWidth: 2,
            color: AppColors.accent,
          ),
        ),
      ));
    } else if (editing) {
      actions.add(LzIconButton(
        icon: Icons.check_rounded,
        tooltip: 'Save',
        accent: true,
        onPressed: onSave,
      ));
    } else {
      actions.add(LzIconButton(
        icon: Icons.edit_outlined,
        tooltip: 'Edit',
        onPressed: onEdit,
      ));
      actions.add(LzIconButton(
        icon: Icons.delete_outline_rounded,
        tooltip: 'Delete',
        color: AppColors.error,
        onPressed: onDelete,
      ));
    }

    return AppBar(
      backgroundColor: AppColors.bgBase,
      surfaceTintColor: Colors.transparent,
      leading: IconButton(
        icon: const Icon(Icons.arrow_back_ios_new_rounded, size: 20),
        color: AppColors.textSecondary,
        onPressed: () => Navigator.of(context).pop(),
      ),
      title: titleWidget,
      actions: actions
          .map((a) => Padding(
                padding: const EdgeInsets.only(right: AppSpacing.xs),
                child: a,
              ))
          .toList(),
    );
  }
}

// ── Read (markdown viewer) ────────────────────────────────────────────────────

class _ReadView extends StatelessWidget {
  const _ReadView({super.key, required this.note});

  final Note note;

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.xl,
        AppSpacing.lg,
        AppSpacing.xl,
        AppSpacing.xxxl,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Title (large, gradient accent if present)
          if (note.title != null) ...[
            ShaderMask(
              shaderCallback: (bounds) =>
                  AppColors.accentGradient.createShader(bounds),
              blendMode: BlendMode.srcIn,
              child: Text(
                note.title!,
                style: AppText.headline.copyWith(color: Colors.white),
              ),
            ),
            const SizedBox(height: AppSpacing.md),
          ],
          // Tags
          if (note.tags.isNotEmpty) ...[
            Wrap(
              spacing: AppSpacing.xs,
              runSpacing: AppSpacing.xs,
              children:
                  note.tags.map((t) => LzChip(label: t, dense: true)).toList(),
            ),
            const SizedBox(height: AppSpacing.lg),
          ],
          // Markdown body — clean token-driven stylesheet
          MarkdownBody(
            data: note.content,
            selectable: true,
            styleSheet: _markdownStyle(),
          ),
          const SizedBox(height: AppSpacing.xl),
          // Timestamp footer
          Text(
            'Updated ${_formatDate(note.updatedAt)}',
            style: AppText.caption,
          ),
        ],
      ),
    );
  }

  MarkdownStyleSheet _markdownStyle() {
    return MarkdownStyleSheet(
      p: AppText.bodyL,
      h1: AppText.headline,
      h2: AppText.titleL,
      h3: AppText.title,
      code: AppText.body.copyWith(
        fontFamily: 'monospace',
        backgroundColor: AppColors.bgSurfaceElevated,
        color: AppColors.accent,
      ),
      codeblockDecoration: BoxDecoration(
        color: AppColors.bgSurfaceElevated,
        borderRadius: AppRadii.rMd,
        border: Border.all(color: AppColors.borderSubtle),
      ),
      blockquoteDecoration: BoxDecoration(
        border: Border(
          left: BorderSide(
            color: AppColors.accent.withValues(alpha: 0.6),
            width: 3,
          ),
        ),
        color: AppColors.bgSurface,
      ),
      blockquote: AppText.body.copyWith(
        color: AppColors.textSecondary,
        fontStyle: FontStyle.italic,
      ),
      a: AppText.body.copyWith(
        color: AppColors.accent,
        decoration: TextDecoration.underline,
        decorationColor: AppColors.accent,
      ),
      listBullet: AppText.body,
      horizontalRuleDecoration: BoxDecoration(
        border: Border(
          top: BorderSide(color: AppColors.borderSubtle),
        ),
      ),
    );
  }

  String _formatDate(String iso) {
    if (iso.isEmpty) return '';
    try {
      final dt = DateTime.parse(iso).toLocal();
      return '${dt.year}-${_pad(dt.month)}-${_pad(dt.day)} '
          '${_pad(dt.hour)}:${_pad(dt.minute)}';
    } catch (_) {
      return iso;
    }
  }

  String _pad(int n) => n.toString().padLeft(2, '0');
}

// ── Edit (raw markdown editor) ────────────────────────────────────────────────

class _EditView extends StatelessWidget {
  const _EditView({
    super.key,
    required this.contentCtrl,
  });

  final TextEditingController contentCtrl;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.xl,
        AppSpacing.md,
        AppSpacing.xl,
        AppSpacing.xl,
      ),
      child: LzTextField(
        controller: contentCtrl,
        hint: 'Write markdown here…',
        minLines: 12,
        maxLines: null,
        autofocus: true,
        keyboardType: TextInputType.multiline,
        textInputAction: TextInputAction.newline,
      ),
    );
  }
}
