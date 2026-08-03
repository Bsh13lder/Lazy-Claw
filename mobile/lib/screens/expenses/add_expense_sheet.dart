import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/core/project_resolver.dart';
import 'package:lazyclaw_mobile/core/smart_add_expense_parser.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/providers/budgets_provider.dart';
import 'package:lazyclaw_mobile/screens/tasks/smart_add_controller.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import 'expense_project_field.dart';
import 'expense_project_suggestion_strip.dart';

/// `AddProjectSheet` used to live in this file; it now has its own. Re-exported
/// so the existing `import '.../add_expense_sheet.dart' show AddProjectSheet;`
/// call sites (Tasks screen, Add Task sheet, Money screen) keep working.
export 'add_project_sheet.dart';

/// Bottom sheet for adding a new expense.
///
/// The Description field is "smart" (Todoist-style, mirroring the Add Task
/// sheet's title field): typing e.g. `spent on #clubbay 25` recognizes the
/// amount and the `#project` token live and PRE-FILLS the Amount field + the
/// project picker below. A manual edit to either always wins over a later
/// re-parse (`_amountTouched`/`_projectTouched`, mirroring the task sheet's
/// `_categoryTouched`).
///
/// FIELD ORDER (2026-08-03): Description is FIRST and autofocused, Amount is
/// second. Opening on Amount raised a numeric keypad, which fought the whole
/// quick-typing interaction — the user has to dismiss/retarget before they can
/// use the one field that fills in everything else. Amount keeps every
/// behavior it had; only its position moved.
///
/// Calls [onSubmit] with (projectId, amount, description, vendor?) — the same
/// 4-arg shape whether the values came from typing or manual entry, so quick-
/// typing can't introduce a new currency (or any other) divergence from the
/// existing form-based add path. Caller is responsible for invoking
/// [BudgetsNotifier.addExpense]; see `showAddExpenseForTaskSheet`
/// (add_expense_for_task.dart) for the task/sub-task-scoped variant.
class AddExpenseSheet extends ConsumerStatefulWidget {
  const AddExpenseSheet({
    super.key,
    required this.projects,
    required this.initialProjectId,
    required this.onSubmit,
    this.lockedProjectId,
    this.contextLabel,
  });

  final List<Project> projects;
  final String? initialProjectId;
  final Future<bool> Function(
    String projectId,
    double amount,
    String description,
    String? vendor,
  ) onSubmit;

  /// Non-null switches the sheet into TASK-SCOPED mode: the project is fixed
  /// to this id, rendered read-only, and neither the picker nor a typed
  /// `#project` token can change it. See [ExpenseProjectLockedField] for why
  /// re-filing a task's expense elsewhere has to be impossible.
  final String? lockedProjectId;

  /// Optional "you are adding to X" header (e.g. `Sub-task: Buy paint`) so a
  /// scoped add never looks like a plain project expense.
  final String? contextLabel;

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
  /// [AddExpenseSheet.projects] but kept live: a project created FROM INSIDE
  /// this sheet (via the strip's "Create project" row) is refreshed into this
  /// list right after the create succeeds, mirroring the Add Task sheet's
  /// `_projects` (see `add_task_sheet.dart:_createProjectFromSuggestion`).
  late List<Project> _projects;

  /// True when the caller pinned the project (task/sub-task scoped add).
  bool get _projectLocked => widget.lockedProjectId != null;

  @override
  void initState() {
    super.initState();
    _projects = widget.projects;
    // A locked id always wins over `initialProjectId` — the caller may pass
    // both (the Money screen's last-used project is irrelevant once the task
    // dictates the destination).
    _projectId = widget.lockedProjectId ??
        widget.initialProjectId ??
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
      // In task-scoped mode a `#project` token is parsed (so it still drops
      // out of the stored description) but deliberately NOT applied — the task
      // owns the destination project.
      if (!_projectLocked && !_projectTouched && parsed.project != null) {
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
      // Pop `true` so a scoped caller (showAddExpenseForTaskSheet) can await
      // the outcome. Harmless on the Money screen's `show<void>` route —
      // `void` accepts any result and that call site ignores it.
      Navigator.pop(context, true);
    } else {
      setState(() => _loading = false);
    }
  }

  /// The locked project's display name, or null when it isn't in the cached
  /// project list yet.
  String? get _lockedProjectName {
    final id = widget.lockedProjectId;
    if (id == null) return null;
    return _projects.where((p) => p.id == id).firstOrNull?.name;
  }

  @override
  Widget build(BuildContext context) {
    // The strip only offers to disambiguate/create when the live-parsed
    // `#`/`/` token has NO unambiguous existing-project match — an
    // unambiguous match is already silently applied by `_onDescriptionChanged`
    // (see the plan's project-resolution semantics). It is suppressed entirely
    // in task-scoped mode: picking/creating a project there would be a no-op
    // at best and a mis-filed expense at worst.
    final suggestToken = _parsed.project;
    final showSuggestions = !_projectLocked &&
        suggestToken != null &&
        _descFocusNode.hasFocus &&
        !_projectTouched &&
        resolveProjectMatch(suggestToken, _projects) == null;

    return LzFloatingSubmitLayout(
      // Square, always-on-screen submit. It replaces the old full-width
      // "Add Expense" button, which a grown form pushed below the sheet's
      // bottom edge — the user's report was literally "there is no save sign".
      submit: LzFloatingSubmit(
        key: const Key('expense-submit-fab'),
        icon: Icons.add_rounded,
        tooltip: 'Add expense',
        loading: _loading,
        onPressed: _loading ? null : _submit,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (widget.contextLabel != null) ...[
            Align(
              alignment: Alignment.centerLeft,
              child: LzPill(
                key: const Key('expense-context-label'),
                label: widget.contextLabel!,
                icon: Icons.link_rounded,
              ),
            ),
            const SizedBox(height: AppSpacing.lg),
          ],
          // Description — FIRST and autofocused because it is the smart field:
          // type "spent on #clubbay 25" and the Amount + project below fill in
          // live. See the class doc for why this beats opening on Amount.
          LzTextField(
            controller: _descController,
            fieldKey: const Key('expense-description-field'),
            focusNode: _descFocusNode,
            label: 'Description',
            hint: 'e.g. "spent on #clubbay 25"',
            prefixIcon: Icons.notes_rounded,
            textInputAction: TextInputAction.next,
            autofocus: true,
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
          // Amount — pre-filled live from the Description field above; a
          // manual edit here always wins over a later re-parse.
          LzTextField(
            controller: _amountCtrl,
            fieldKey: const Key('expense-amount-field'),
            label: 'Amount',
            hint: '0.00',
            prefixIcon: Icons.attach_money_rounded,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            textInputAction: TextInputAction.next,
            errorText: _amountError,
            onChanged: (v) {
              if (v != _lastAutoFilledAmountText) _amountTouched = true;
              if (_amountError != null) setState(() => _amountError = null);
            },
          ),
          const SizedBox(height: AppSpacing.lg),
          // Vendor (optional) — last field, so it submits.
          LzTextField(
            controller: _vendorCtrl,
            label: 'Vendor (optional)',
            hint: 'Merchant or source',
            prefixIcon: Icons.storefront_outlined,
            textInputAction: TextInputAction.done,
            onSubmitted: (_) => _submit(),
          ),
          const SizedBox(height: AppSpacing.lg),
          if (_projectLocked)
            ExpenseProjectLockedField(projectName: _lockedProjectName)
          else
            ExpenseProjectPicker(
              projects: _projects,
              selectedId: _projectId,
              onChanged: (id) => setState(() {
                _projectId = id;
                _projectTouched = true;
              }),
            ),
        ],
      ),
    );
  }
}
