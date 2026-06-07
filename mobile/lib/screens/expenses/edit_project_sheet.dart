import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import 'project_color_picker.dart';

/// Bottom sheet for managing an existing project: rename it, pick a color, or
/// delete it. The actual mutations run through the caller-supplied callbacks
/// (wired to the budgets provider) so this widget stays UI-only.
///
/// On Save it applies whichever of name/color actually changed; on Delete it
/// confirms first (via [LzConfirm]) then runs [onDelete]. Each callback returns
/// true on success so the sheet only closes when the write landed.
class EditProjectSheet extends StatefulWidget {
  const EditProjectSheet({
    super.key,
    required this.project,
    required this.onRename,
    required this.onSetColor,
    required this.onDelete,
  });

  final Project project;

  /// Rename the project. Returns true on success.
  final Future<bool> Function(String name) onRename;

  /// Set the project's accent color (a `"#RRGGBB"` hex). Returns true on success.
  final Future<bool> Function(String color) onSetColor;

  /// Delete the project. Returns true on success.
  final Future<bool> Function() onDelete;

  @override
  State<EditProjectSheet> createState() => _EditProjectSheetState();
}

class _EditProjectSheetState extends State<EditProjectSheet> {
  late final TextEditingController _nameCtrl;
  late String? _selectedColor;
  bool _saving = false;
  bool _deleting = false;
  String? _nameError;

  @override
  void initState() {
    super.initState();
    _nameCtrl = TextEditingController(text: widget.project.name);
    _selectedColor = widget.project.color;
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    super.dispose();
  }

  bool get _busy => _saving || _deleting;

  Future<void> _save() async {
    final name = _nameCtrl.text.trim();
    if (name.isEmpty) {
      setState(() => _nameError = 'Project name is required');
      return;
    }

    setState(() {
      _saving = true;
      _nameError = null;
    });

    var ok = true;
    if (name != widget.project.name) {
      ok = await widget.onRename(name);
    }
    if (ok &&
        _selectedColor != null &&
        _selectedColor != widget.project.color) {
      ok = await widget.onSetColor(_selectedColor!);
    }

    if (!mounted) return;
    if (ok) {
      Navigator.pop(context);
    } else {
      setState(() => _saving = false);
    }
  }

  Future<void> _delete() async {
    final confirmed = await LzConfirm.show(
      context,
      title: 'Delete project?',
      message:
          '"${widget.project.name}" and all its expenses will be removed.',
      confirmLabel: 'Delete',
      danger: true,
    );
    if (!confirmed || !mounted) return;

    setState(() => _deleting = true);
    final ok = await widget.onDelete();
    if (!mounted) return;
    if (ok) {
      Navigator.pop(context);
    } else {
      setState(() => _deleting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        LzTextField(
          controller: _nameCtrl,
          label: 'Project name',
          hint: 'e.g. Marketing Q3',
          prefixIcon: Icons.folder_outlined,
          textInputAction: TextInputAction.done,
          autofocus: false,
          enabled: !_busy,
          errorText: _nameError,
          onChanged: (_) {
            if (_nameError != null) setState(() => _nameError = null);
          },
          onSubmitted: (_) => _busy ? null : _save(),
        ),
        const SizedBox(height: AppSpacing.xl),
        Text(
          'Color',
          style: AppText.label.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(height: AppSpacing.md),
        ProjectColorSwatches(
          selected: _selectedColor,
          onSelected: (hex) => setState(() => _selectedColor = hex),
        ),
        const SizedBox(height: AppSpacing.xl),
        LzButton.primary(
          label: 'Save',
          icon: Icons.check_rounded,
          loading: _saving,
          expand: true,
          onPressed: _busy ? null : _save,
        ),
        const SizedBox(height: AppSpacing.md),
        LzButton.danger(
          label: 'Delete project',
          icon: Icons.delete_outline_rounded,
          loading: _deleting,
          expand: true,
          onPressed: _busy ? null : _delete,
        ),
      ],
    );
  }
}
