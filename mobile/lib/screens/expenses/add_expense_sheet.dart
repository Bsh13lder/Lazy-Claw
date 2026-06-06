import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

/// Bottom sheet for adding a new expense.
///
/// Calls [onSubmit] with (projectId, amount, description, vendor?).
/// Caller is responsible for invoking [budgetsProvider.addExpense].
class AddExpenseSheet extends StatefulWidget {
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
  State<AddExpenseSheet> createState() => _AddExpenseSheetState();
}

class _AddExpenseSheetState extends State<AddExpenseSheet> {
  final _amountCtrl = TextEditingController();
  final _descCtrl = TextEditingController();
  final _vendorCtrl = TextEditingController();
  late String? _projectId;
  bool _loading = false;
  String? _amountError;

  @override
  void initState() {
    super.initState();
    _projectId = widget.initialProjectId ??
        (widget.projects.isNotEmpty ? widget.projects.first.id : null);
  }

  @override
  void dispose() {
    _amountCtrl.dispose();
    _descCtrl.dispose();
    _vendorCtrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final amountText = _amountCtrl.text.trim();
    final desc = _descCtrl.text.trim();
    final vendor = _vendorCtrl.text.trim();

    final amount = double.tryParse(amountText);
    if (amount == null || amount <= 0) {
      setState(() => _amountError = 'Enter a valid amount');
      return;
    }
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
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Amount — first, biggest, most important.
        LzTextField(
          controller: _amountCtrl,
          label: 'Amount',
          hint: '0.00',
          prefixIcon: Icons.attach_money_rounded,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          textInputAction: TextInputAction.next,
          errorText: _amountError,
          autofocus: true,
          onChanged: (_) {
            if (_amountError != null) setState(() => _amountError = null);
          },
        ),
        const SizedBox(height: AppSpacing.lg),
        // Description.
        LzTextField(
          controller: _descCtrl,
          label: 'Description',
          hint: 'What was this for?',
          prefixIcon: Icons.notes_rounded,
          textInputAction: TextInputAction.next,
        ),
        const SizedBox(height: AppSpacing.lg),
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
          projects: widget.projects,
          selectedId: _projectId,
          onChanged: (id) => setState(() => _projectId = id),
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

  final Future<bool> Function(String name, double? budget) onSubmit;

  @override
  State<AddProjectSheet> createState() => _AddProjectSheetState();
}

class _AddProjectSheetState extends State<AddProjectSheet> {
  final _nameCtrl = TextEditingController();
  final _budgetCtrl = TextEditingController();
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

    final ok = await widget.onSubmit(name, budget);
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
