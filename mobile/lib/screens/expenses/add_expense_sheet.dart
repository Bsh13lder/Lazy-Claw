import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/core/project_resolver.dart';
import 'package:lazyclaw_mobile/core/smart_add_expense_parser.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/providers/budgets_provider.dart';
import 'package:lazyclaw_mobile/screens/tasks/smart_add_controller.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import 'expense_project_suggestion_strip.dart';
import 'project_color_picker.dart';
import 'project_date_chips.dart';

/// Bottom sheet for adding a new expense.
///
/// The Description field is "smart" (Todoist-style, mirroring the Add Task
/// sheet's title field): typing e.g. `spent on #clubbay 25` recognizes the
/// amount and the `#project` token live and PRE-FILLS the Amount field + the
/// project picker below. A manual edit to either always wins over a later
/// re-parse (`_amountTouched`/`_projectTouched`, mirroring the task sheet's
/// `_categoryTouched`).
///
/// Calls [onSubmit] with (projectId, amount, description, vendor?) — the same
/// 4-arg shape whether the values came from typing or manual entry, so quick-
/// typing can't introduce a new currency (or any other) divergence from the
/// existing form-based add path. Caller is responsible for invoking
/// [budgetsProvider.addExpense].
class AddExpenseSheet extends ConsumerStatefulWidget {
  const AddExpenseSheet({
    super.key,
    required this.projects,
    required this.initialProjectId,
    required this.onSubmit,
  });

  final List<Project> projects;
  final String? initialProjectId;
  final Future<bool> Function(
    String projectId,
    double amount,
    String description,
    String? vendor,
  ) onSubmit;

  @override
  ConsumerState<AddExpenseSheet> createState() => _AddExpenseSheetState();
}

class _AddExpenseSheetState extends ConsumerState<AddExpenseSheet> {
  final _amountCtrl = TextEditingController();
  final _descController = SmartAddController();
  final _descFocusNode = FocusNode();
  final _vendorCtrl = TextEditingController();
  late String? _projectId;
  bool _loading = false;
  String? _amountError;

  /// Live parse of the current description text. Drives the pre-fills below
  /// and the `#`/`/` project-suggestion strip.
  ParsedExpense _parsed = const ParsedExpense(cleanDescription: '');

  /// Manual-override-wins tracking, mirroring the Add Task sheet's
  /// `_categoryTouched`: once true, a live re-parse never overwrites the
  /// field again.
  bool _amountTouched = false;
  bool _projectTouched = false;

  /// The last value THIS widget wrote into [_amountCtrl] (as opposed to one
  /// the user typed). [TextField.onChanged] fires for both a programmatic
  /// `.text =` write and a real keystroke, so this sentinel is how the Amount
  /// field's `onChanged` tells its own auto-fill apart from a genuine manual
  /// edit — a manual edit is any change whose new text doesn't match it.
  String? _lastAutoFilledAmountText;

  /// The projects offered by the `#`/`/` suggestion strip. Seeded from
  /// [widget.projects] but kept live: a project created FROM INSIDE this
  /// sheet (via the strip's "Create project" row) is refreshed into this
  /// list right after the create succeeds, mirroring the Add Task sheet's
  /// `_projects` (see `add_task_sheet.dart:_createProjectFromSuggestion`).
  late List<Project> _projects;

  @override
  void initState() {
    super.initState();
    _projects = widget.projects;
    _projectId = widget.initialProjectId ??
        (_projects.isNotEmpty ? _projects.first.id : null);
    _descFocusNode.addListener(_handleDescFocusChange);
  }

  @override
  void dispose() {
    _descFocusNode
      ..removeListener(_handleDescFocusChange)
      ..dispose();
    _amountCtrl.dispose();
    _descController.dispose();
    _vendorCtrl.dispose();
    super.dispose();
  }

  /// Rebuilds so the `#`/`/` suggestion strip appears/disappears with focus,
  /// same as the Add Task sheet's `_handleTitleFocusChange`.
  void _handleDescFocusChange() {
    if (mounted) setState(() {});
  }

  /// Format a parsed amount for the Amount field: whole numbers show with no
  /// decimals ("25"), everything else with exactly two ("45.50") — matching
  /// what the user is most likely to have typed.
  String _formatAmountForField(double amount) =>
      amount == amount.truncateToDouble()
          ? amount.toInt().toString()
          : amount.toStringAsFixed(2);

  void _applyParsedAmount(double amount) {
    final text = _formatAmountForField(amount);
    _lastAutoFilledAmountText = text;
    _amountCtrl.text = text;
  }

  void _onDescriptionChanged(String value) {
    final parsed = parseSmartExpense(value);
    // Push the fresh spans into the controller so the field highlights the
    // recognized amount/project tokens live.
    _descController.tokens = parsed.tokens;
    setState(() {
      _parsed = parsed;
      if (!_amountTouched && parsed.amount != null) {
        _applyParsedAmount(parsed.amount!);
      }
      if (!_projectTouched && parsed.project != null) {
        final match = resolveProjectMatch(parsed.project!, _projects);
        if (match != null) _projectId = match.id;
      }
    });
  }

  /// Apply a suggestion-strip pick (or a freshly-created project of the same
  /// name) as the (touched) project — the raw `#`/`/` token stays in the
  /// description text itself; only the project SELECTION is affected, unlike
  /// the Add Task sheet (which strips the token from the title because there
  /// the project becomes a separate category, not part of the task text).
  void _applyProjectSuggestion(String projectName) {
    final match = resolveProjectMatch(projectName, _projects);
    setState(() {
      if (match != null) _projectId = match.id;
      _projectTouched = true;
    });
  }

  /// The suggestion strip's "Create project '{token}'" row: creates the
  /// project via the shared provider, refreshes the live [_projects] list so
  /// a re-typed token doesn't offer to create it again, then applies it.
  /// Mirrors `add_task_sheet.dart:_createProjectFromSuggestion`.
  Future<void> _createProjectFromSuggestion(String token) async {
    final ok = await ref.read(budgetsProvider.notifier).createProject(token);
    if (ok && mounted) {
      _projects = ref.read(budgetsProvider).projects;
      _applyProjectSuggestion(token);
    }
  }

  Future<void> _submit() async {
    final amountText = _amountCtrl.text.trim().replaceAll(',', '.');
    final vendor = _vendorCtrl.text.trim();

    final amount = double.tryParse(amountText);
    if (amount == null || amount <= 0) {
      setState(() => _amountError = 'Enter a valid amount');
      return;
    }

    // Re-parse from the live controller so a submit-via-keyboard can't race
    // the onChanged callback, mirroring the Add Task sheet's `_submit`.
    final parsed = parseSmartExpense(_descController.text);
    final clean = parsed.cleanDescription.trim();
    final desc = clean.isNotEmpty ? clean : _descController.text.trim();

    if (desc.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Description is required')),
      );
      return;
    }
    if (_projectId == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Select a project')),
      );
      return;
    }

    setState(() {
      _loading = true;
      _amountError = null;
    });

    final ok = await widget.onSubmit(
      _projectId!,
      amount,
      desc,
      vendor.isEmpty ? null : vendor,
    );

    if (!mounted) return;
    if (ok) {
      Navigator.pop(context);
    } else {
      setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    // The strip only offers to disambiguate/create when the live-parsed
    // `#`/`/` token has NO unambiguous existing-project match — an
    // unambiguous match is already silently applied by `_onDescriptionChanged`
    // (see the plan's project-resolution semantics).
    final suggestToken = _parsed.project;
    final showSuggestions = suggestToken != null &&
        _descFocusNode.hasFocus &&
        !_projectTouched &&
        resolveProjectMatch(suggestToken, _projects) == null;

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Amount — first, biggest, most important. Pre-filled live from the
        // Description field below; a manual edit here always wins.
        LzTextField(
          controller: _amountCtrl,
          fieldKey: const Key('expense-amount-field'),
          label: 'Amount',
          hint: '0.00',
          prefixIcon: Icons.attach_money_rounded,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          textInputAction: TextInputAction.next,
          errorText: _amountError,
          autofocus: true,
          onChanged: (v) {
            if (v != _lastAutoFilledAmountText) _amountTouched = true;
            if (_amountError != null) setState(() => _amountError = null);
          },
        ),
        const SizedBox(height: AppSpacing.lg),
        // Description — the smart field: type "spent on #clubbay 25" and the
        // amount + project above pre-fill live.
        LzTextField(
          controller: _descController,
          fieldKey: const Key('expense-description-field'),
          focusNode: _descFocusNode,
          label: 'Description',
          hint: 'e.g. "spent on #clubbay 25"',
          prefixIcon: Icons.notes_rounded,
          textInputAction: TextInputAction.next,
          onChanged: _onDescriptionChanged,
        ),
        if (showSuggestions)
          ExpenseProjectSuggestionStrip(
            token: suggestToken,
            projects: _projects,
            onSelect: _applyProjectSuggestion,
            onCreate: _createProjectFromSuggestion,
          ),
        const SizedBox(height: AppSpacing.xs),
        Text(
          '25 · €45.50 · 40 eur · #project',
          style: AppText.caption.copyWith(color: AppColors.textMuted),
        ),
        const SizedBox(height: AppSpacing.md),
        // Vendor (optional).
        LzTextField(
          controller: _vendorCtrl,
          label: 'Vendor (optional)',
          hint: 'Merchant or source',
          prefixIcon: Icons.storefront_outlined,
          textInputAction: TextInputAction.done,
          onSubmitted: (_) => _submit(),
        ),
        const SizedBox(height: AppSpacing.lg),
        // Project picker.
        _ProjectPicker(
          projects: _projects,
          selectedId: _projectId,
          onChanged: (id) => setState(() {
            _projectId = id;
            _projectTouched = true;
          }),
        ),
        const SizedBox(height: AppSpacing.xl),
        // Submit.
        LzButton.primary(
          label: 'Add Expense',
          icon: Icons.add_rounded,
          loading: _loading,
          expand: true,
          onPressed: _loading ? null : _submit,
        ),
      ],
    );
  }
}

class _ProjectPicker extends StatelessWidget {
  const _ProjectPicker({
    required this.projects,
    required this.selectedId,
    required this.onChanged,
  });

  final List<Project> projects;
  final String? selectedId;
  final ValueChanged<String?> onChanged;

  @override
  Widget build(BuildContext context) {
    if (projects.isEmpty) {
      return Container(
        padding: const EdgeInsets.all(AppSpacing.md),
        decoration: BoxDecoration(
          color: AppColors.bgSurfaceElevated,
          borderRadius: AppRadii.rMd,
          border: Border.all(color: AppColors.borderDefault),
        ),
        child: Text(
          'No projects — create one first',
          style: AppText.body.copyWith(color: AppColors.textMuted),
        ),
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Project',
          style: AppText.label.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(height: AppSpacing.sm),
        Container(
          decoration: BoxDecoration(
            color: AppColors.bgSurfaceElevated,
            borderRadius: AppRadii.rMd,
            border: Border.all(color: AppColors.borderDefault),
          ),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String>(
              value: selectedId,
              isExpanded: true,
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
              dropdownColor: AppColors.bgSurfaceElevated,
              style: AppText.body,
              icon: const Icon(
                Icons.keyboard_arrow_down_rounded,
                color: AppColors.textMuted,
              ),
              hint: Text(
                'Select project',
                style: AppText.body.copyWith(color: AppColors.textMuted),
              ),
              items: projects
                  .map(
                    (p) => DropdownMenuItem<String>(
                      value: p.id,
                      child: Text(p.name, style: AppText.body),
                    ),
                  )
                  .toList(),
              onChanged: onChanged,
            ),
          ),
        ),
      ],
    );
  }
}

/// Bottom sheet for creating a new project.
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
