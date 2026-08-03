import 'package:flutter/material.dart';

import '../../ui/ui.dart';
import 'project_color_picker.dart';
import 'project_date_chips.dart';

/// Bottom sheet for creating a new project.
///
/// Extracted out of `add_expense_sheet.dart` (2026-08-03) — it is a separate
/// surface that just happened to live in the same file, and that file needed
/// the room. `add_expense_sheet.dart` re-exports this so the three existing
/// import sites (Money screen, Tasks screen, Add Task sheet) keep working
/// unchanged.
class AddProjectSheet extends StatefulWidget {
  const AddProjectSheet({
    super.key,
    required this.onSubmit,
  });

  /// Called with (name, budget?, color?, startDate?, dueDate?). The color is a
  /// `"#RRGGBB"` hex string; the dates are `yyyy-MM-dd` strings — each null
  /// when the user left it unset.
  final Future<bool> Function(
    String name,
    double? budget,
    String? color,
    String? startDate,
    String? dueDate,
  ) onSubmit;

  @override
  State<AddProjectSheet> createState() => _AddProjectSheetState();
}

class _AddProjectSheetState extends State<AddProjectSheet> {
  final _nameCtrl = TextEditingController();
  final _budgetCtrl = TextEditingController();
  String? _selectedColor;
  String? _startDate;
  String? _dueDate;
  bool _loading = false;
  String? _nameError;

  @override
  void dispose() {
    _nameCtrl.dispose();
    _budgetCtrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final name = _nameCtrl.text.trim();
    if (name.isEmpty) {
      setState(() => _nameError = 'Project name is required');
      return;
    }

    final budget = double.tryParse(_budgetCtrl.text.trim());
    setState(() {
      _loading = true;
      _nameError = null;
    });

    final ok = await widget.onSubmit(
        name, budget, _selectedColor, _startDate, _dueDate);
    if (!mounted) return;
    if (ok) {
      Navigator.pop(context);
    } else {
      setState(() => _loading = false);
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
          textInputAction: TextInputAction.next,
          autofocus: true,
          errorText: _nameError,
          onChanged: (_) {
            if (_nameError != null) setState(() => _nameError = null);
          },
        ),
        const SizedBox(height: AppSpacing.lg),
        LzTextField(
          controller: _budgetCtrl,
          label: 'Budget (optional)',
          hint: '0.00',
          prefixIcon: Icons.account_balance_wallet_outlined,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          textInputAction: TextInputAction.done,
          onSubmitted: (_) => _submit(),
        ),
        const SizedBox(height: AppSpacing.lg),
        Text(
          'Color (optional)',
          style: AppText.label.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(height: AppSpacing.md),
        ProjectColorSwatches(
          selected: _selectedColor,
          onSelected: (hex) => setState(() => _selectedColor = hex),
        ),
        const SizedBox(height: AppSpacing.lg),
        Text(
          'Time frame (optional)',
          style: AppText.label.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(height: AppSpacing.md),
        ProjectDateChips(
          startDate: _startDate,
          dueDate: _dueDate,
          onStartChanged: (v) => setState(() => _startDate = v),
          onDueChanged: (v) => setState(() => _dueDate = v),
        ),
        const SizedBox(height: AppSpacing.xl),
        LzButton.primary(
          label: 'Create Project',
          icon: Icons.create_new_folder_outlined,
          loading: _loading,
          expand: true,
          onPressed: _loading ? null : _submit,
        ),
      ],
    );
  }
}
