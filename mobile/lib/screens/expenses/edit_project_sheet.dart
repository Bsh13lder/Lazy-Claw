import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import 'project_color_picker.dart';
import 'project_date_chips.dart';

/// Bottom sheet for managing an existing project: rename it, edit its budget,
/// pick a color, or delete it. The actual mutations run through the
/// caller-supplied callbacks (wired to the budgets provider) so this widget
/// stays UI-only.
///
/// On Save it applies whichever of name/budget/color actually changed; on
/// Delete it confirms first (via [LzConfirm]) then runs [onDelete]. Each
/// callback returns true on success so the sheet only closes when the write
/// landed.
class EditProjectSheet extends StatefulWidget {
  const EditProjectSheet({
    super.key,
    required this.project,
    required this.onRename,
    required this.onSetBudget,
    required this.onSetColor,
    required this.onDelete,
    this.onSetDates,
    this.onOpenBudgetLog,
  });

  final Project project;

  /// Rename the project. Returns true on success.
  final Future<bool> Function(String name) onRename;

  /// Set the project's total budget amount. Returns true on success.
  final Future<bool> Function(double budget) onSetBudget;

  /// Set the project's accent color (a `"#RRGGBB"` hex). Returns true on success.
  final Future<bool> Function(String color) onSetColor;

  /// Delete the project. Returns true on success.
  final Future<bool> Function() onDelete;

  /// Set the project's time frame. Each value is a `yyyy-MM-dd` day or `''`
  /// (the clear sentinel — the server leniently nulls empty values). Returns
  /// true on success. When null the time-frame chips are hidden.
  final Future<bool> Function(String startDate, String dueDate)? onSetDates;

  /// Opens the Budget ledger sheet (add-to-budget top-ups + the credits/debits
  /// Log). When null the affordance is hidden.
  final VoidCallback? onOpenBudgetLog;

  @override
  State<EditProjectSheet> createState() => _EditProjectSheetState();
}

class _EditProjectSheetState extends State<EditProjectSheet> {
  late final TextEditingController _nameCtrl;
  late final TextEditingController _budgetCtrl;
  late String? _selectedColor;

  /// Working copy of the time frame (`yyyy-MM-dd` or null = unset). Seeded from
  /// the project (an `''` cache value reads as unset). Save writes them only
  /// when either changed, sending `''` to clear.
  late String? _startDate;
  late String? _dueDate;

  bool _saving = false;
  bool _deleting = false;
  String? _nameError;
  String? _budgetError;

  @override
  void initState() {
    super.initState();
    _nameCtrl = TextEditingController(text: widget.project.name);
    _budgetCtrl =
        TextEditingController(text: _formatBudget(widget.project.budget));
    _selectedColor = widget.project.color;
    _startDate = _normDate(widget.project.startDate);
    _dueDate = _normDate(widget.project.dueDate);
  }

  /// Null/blank → unset; anything else is kept as-is (`yyyy-MM-dd`).
  static String? _normDate(String? v) =>
      (v == null || v.isEmpty) ? null : v;

  @override
  void dispose() {
    _nameCtrl.dispose();
    _budgetCtrl.dispose();
    super.dispose();
  }

  bool get _busy => _saving || _deleting;

  /// Render the stored budget into the editable field: blank when unset (0),
  /// no trailing decimals on whole amounts.
  static String _formatBudget(double v) {
    if (v <= 0) return '';
    if (v == v.truncateToDouble()) return v.toInt().toString();
    return v.toStringAsFixed(2);
  }

  Future<void> _save() async {
    final name = _nameCtrl.text.trim();
    if (name.isEmpty) {
      setState(() => _nameError = 'Project name is required');
      return;
    }

    // Budget: blank → 0 (cleared); otherwise must parse to a non-negative number.
    final budgetText = _budgetCtrl.text.trim();
    double? budget;
    if (budgetText.isEmpty) {
      budget = 0.0;
    } else {
      budget = double.tryParse(budgetText);
      if (budget == null || budget < 0) {
        setState(() => _budgetError = 'Enter a valid amount');
        return;
      }
    }

    setState(() {
      _saving = true;
      _nameError = null;
      _budgetError = null;
    });

    var ok = true;
    if (name != widget.project.name) {
      ok = await widget.onRename(name);
    }
    if (ok && budget != widget.project.budget) {
      ok = await widget.onSetBudget(budget);
    }
    if (ok &&
        _selectedColor != null &&
        _selectedColor != widget.project.color) {
      ok = await widget.onSetColor(_selectedColor!);
    }
    // Time frame: only write when either date actually changed. Both current
    // values ride together, with '' as the clear sentinel for an unset chip.
    final datesChanged = _startDate != _normDate(widget.project.startDate) ||
        _dueDate != _normDate(widget.project.dueDate);
    if (ok && widget.onSetDates != null && datesChanged) {
      ok = await widget.onSetDates!(_startDate ?? '', _dueDate ?? '');
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
        const SizedBox(height: AppSpacing.lg),
        // Budget — the project's total allocated budget. Blank clears it back to
        // "no budget set". Mirrors the web project budget control.
        LzTextField(
          controller: _budgetCtrl,
          label: 'Budget',
          hint: '0.00',
          prefixIcon: Icons.account_balance_wallet_outlined,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          textInputAction: TextInputAction.done,
          enabled: !_busy,
          errorText: _budgetError,
          onChanged: (_) {
            if (_budgetError != null) setState(() => _budgetError = null);
          },
          onSubmitted: (_) => _busy ? null : _save(),
        ),
        // Budget ledger — add-to-budget top-ups + the credits/debits Log. The
        // field above sets the TOTAL directly; this opens the sourced-top-up
        // ledger (mirrors the web "+ Add budget" / "📋 Log").
        if (widget.onOpenBudgetLog != null) ...[
          const SizedBox(height: AppSpacing.md),
          LzButton.secondary(
            label: 'Budget log & top-ups',
            icon: Icons.receipt_long_outlined,
            expand: true,
            onPressed: _busy ? null : widget.onOpenBudgetLog,
          ),
        ],
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
        if (widget.onSetDates != null) ...[
          const SizedBox(height: AppSpacing.xl),
          Text(
            'Time frame',
            style: AppText.label.copyWith(color: AppColors.textSecondary),
          ),
          const SizedBox(height: AppSpacing.md),
          ProjectDateChips(
            startDate: _startDate,
            dueDate: _dueDate,
            enabled: !_busy,
            onStartChanged: (v) => setState(() => _startDate = v),
            onDueChanged: (v) => setState(() => _dueDate = v),
          ),
        ],
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
