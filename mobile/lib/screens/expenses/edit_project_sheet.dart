import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/core/autosave.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../widgets/autosave_indicator.dart';
import 'project_color_picker.dart';
import 'project_date_chips.dart';

/// Stable field keys, shared with the tests so a rename can't orphan them and
/// so matching never has to go through a text lookup (which stops resolving the
/// moment the field is edited).
const Key kEditProjectNameKey = Key('edit-project-name');
const Key kEditProjectBudgetKey = Key('edit-project-budget');

/// Bottom sheet for managing an existing project: rename it, edit its budget,
/// pick a color, or delete it. The actual mutations run through the
/// caller-supplied callbacks (wired to the budgets provider) so this widget
/// stays UI-only.
///
/// It AUTO-SAVES: the name and budget a beat after typing stops, the colour
/// and date chips the moment they change, and anything pending is flushed when
/// the sheet is dismissed or the app is backgrounded. Save now means "commit
/// now and close"; Delete still confirms (via [LzConfirm]) first.
///
/// It applies whichever of name/budget/color/dates actually changed. Each
/// callback returns true on success — a false is treated as a real failure and
/// surfaced, not swallowed.
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

  bool _deleting = false;
  String? _nameError;
  String? _budgetError;

  /// What is currently PERSISTED, per field.
  ///
  /// One baseline per callback, because there is one round-trip per callback.
  /// They ADVANCE on every successful write — freezing them at open (which is
  /// what the old one-shot Save could safely do) would mean a value the user
  /// auto-saves and then reverts compares equal to the opening snapshot and is
  /// never written back.
  late String _savedName;
  late double _savedBudget;
  late String? _savedColor;
  late String? _savedStart;
  late String? _savedDue;

  late final AutosaveController _autosave;
  AutosaveStatus _status = AutosaveStatus.idle;
  bool _disposing = false;

  @override
  void initState() {
    super.initState();
    _nameCtrl = TextEditingController(text: widget.project.name);
    _budgetCtrl =
        TextEditingController(text: _formatBudget(widget.project.budget));
    _selectedColor = widget.project.color;
    _startDate = _normDate(widget.project.startDate);
    _dueDate = _normDate(widget.project.dueDate);
    _savedName = widget.project.name;
    // Baseline the budget off what the FIELD shows, never off the raw stored
    // double. `_formatBudget` rounds to 2dp, so a stored 1234.567 renders as
    // "1234.57" — and baselining against 1234.567 made merely opening and
    // closing the sheet write that rounding back. Under auto-save that phantom
    // write churns `updated_at` and, with last-write-wins sync, can clobber a
    // real remote change. The text the user never touched is the only thing
    // that can honestly be called "unchanged". The sibling sheets are immune by
    // construction because they fingerprint their controllers, not their model.
    _savedBudget = _budgetFromText(_budgetCtrl.text);
    _savedColor = widget.project.color;
    _savedStart = _startDate;
    _savedDue = _dueDate;
    _autosave = AutosaveController(onCommit: _commit)
      ..addListener(_onAutosaveStatus)
      ..bindText(_nameCtrl)
      ..bindText(_budgetCtrl);
  }

  /// Null/blank → unset; anything else is kept as-is (`yyyy-MM-dd`).
  static String? _normDate(String? v) =>
      (v == null || v.isEmpty) ? null : v;

  @override
  void dispose() {
    // ORDER IS LOAD-BEARING — see the twin comment in `task_detail_sheet.dart`.
    // `flush` runs [_commit] synchronously as far as the first `await`, so the
    // payload is read off the controllers before they are disposed.
    _disposing = true;
    _autosave.removeListener(_onAutosaveStatus);
    _autosave.flush();
    _autosave.dispose();
    _nameCtrl.dispose();
    _budgetCtrl.dispose();
    super.dispose();
  }

  void _onAutosaveStatus() {
    if (!mounted || _disposing) return;
    setState(() => _status = _autosave.status);
  }

  /// `setState` while alive, a plain assignment once not.
  void _apply(VoidCallback change) {
    if (!mounted || _disposing) {
      change();
      return;
    }
    setState(change);
  }

  /// Apply a DISCRETE edit (colour swatch, date chip) and persist it at once.
  void _editNow(VoidCallback change) {
    setState(change);
    _autosave.markDirtyNow();
  }

  bool get _busy => _status == AutosaveStatus.saving || _deleting;

  /// Render the stored budget into the editable field: blank when unset (0),
  /// no trailing decimals on whole amounts.
  static String _formatBudget(double v) {
    if (v <= 0) return '';
    if (v == v.truncateToDouble()) return v.toInt().toString();
    return v.toStringAsFixed(2);
  }

  /// The budget a field's [text] represents — the exact inverse of
  /// [_formatBudget], using the SAME rules [_commit] applies so the baseline
  /// and the commit can never disagree about what "unchanged" means: blank is
  /// a cleared budget (0), anything else is its parsed value.
  ///
  /// Only ever fed text this sheet itself rendered, so an unparseable value is
  /// unreachable here. It still falls back to 0 rather than throwing: that
  /// matches the blank case, which at worst re-writes a budget the user did
  /// not touch — whereas a null-ish baseline would mark the sheet dirty on
  /// open and fire a phantom write on every close.
  static double _budgetFromText(String text) {
    final trimmed = text.trim();
    if (trimmed.isEmpty) return 0.0;
    return double.tryParse(trimmed) ?? 0.0;
  }

  /// Persist the sheet — the validity gate, then one round-trip per field that
  /// actually differs from what is stored. Called by [AutosaveController].
  ///
  /// A failed callback advances no baseline for that field, so the next edit
  /// retries it; the whole commit is reported as failed (an exception, which
  /// the controller turns into [AutosaveStatus.failed]) rather than silently
  /// pretending the write landed.
  Future<AutosaveOutcome> _commit() async {
    if (_deleting) return AutosaveOutcome.unchanged;

    final name = _nameCtrl.text.trim();
    if (name.isEmpty) {
      _apply(() => _nameError = 'Project name is required');
      return AutosaveOutcome.blocked;
    }

    // Budget: blank → 0 (cleared); otherwise must parse to a non-negative number.
    final budgetText = _budgetCtrl.text.trim();
    double budget;
    if (budgetText.isEmpty) {
      budget = 0.0;
    } else {
      final parsed = double.tryParse(budgetText);
      if (parsed == null || parsed < 0) {
        _apply(() {
          _nameError = null;
          _budgetError = 'Enter a valid amount';
        });
        return AutosaveOutcome.blocked;
      }
      budget = parsed;
    }
    if (_nameError != null || _budgetError != null) {
      _apply(() {
        _nameError = null;
        _budgetError = null;
      });
    }

    // Time frame: both current values ride together, with '' as the clear
    // sentinel for an unset chip.
    final datesChanged = _startDate != _savedStart || _dueDate != _savedDue;
    final colorChanged =
        _selectedColor != null && _selectedColor != _savedColor;
    if (name == _savedName &&
        budget == _savedBudget &&
        !colorChanged &&
        !(datesChanged && widget.onSetDates != null)) {
      return AutosaveOutcome.unchanged;
    }

    var failed = false;
    var wrote = false;
    if (name != _savedName) {
      if (await widget.onRename(name)) {
        _savedName = name;
        wrote = true;
      } else {
        failed = true;
      }
    }
    if (budget != _savedBudget) {
      if (await widget.onSetBudget(budget)) {
        _savedBudget = budget;
        wrote = true;
      } else {
        failed = true;
      }
    }
    if (colorChanged) {
      if (await widget.onSetColor(_selectedColor!)) {
        _savedColor = _selectedColor;
        wrote = true;
      } else {
        failed = true;
      }
    }
    if (datesChanged && widget.onSetDates != null) {
      if (await widget.onSetDates!(_startDate ?? '', _dueDate ?? '')) {
        _savedStart = _startDate;
        _savedDue = _dueDate;
        wrote = true;
      } else {
        failed = true;
      }
    }
    if (failed) throw const _ProjectWriteFailed();
    return wrote ? AutosaveOutcome.written : AutosaveOutcome.unchanged;
  }

  /// Commit whatever is outstanding, then close. A refused or failed write
  /// keeps the sheet open so the user can see (and fix) what went wrong.
  Future<void> _submit() async {
    if (_deleting) return;
    await _autosave.flush();
    if (!mounted) return;
    if (_status == AutosaveStatus.blocked ||
        _status == AutosaveStatus.failed) {
      return;
    }
    Navigator.pop(context);
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

    // Drop anything queued BEFORE flagging: a debounced rename landing after
    // the delete would patch a project on its way out.
    _autosave.cancelPending();
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
          fieldKey: kEditProjectNameKey,
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
          onSubmitted: (_) => _busy ? null : _submit(),
        ),
        const SizedBox(height: AppSpacing.lg),
        // Budget — the project's total allocated budget. Blank clears it back to
        // "no budget set". Mirrors the web project budget control.
        LzTextField(
          controller: _budgetCtrl,
          fieldKey: kEditProjectBudgetKey,
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
          onSubmitted: (_) => _busy ? null : _submit(),
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
          onSelected: (hex) => _editNow(() => _selectedColor = hex),
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
            onStartChanged: (v) => _editNow(() => _startDate = v),
            onDueChanged: (v) => _editNow(() => _dueDate = v),
          ),
        ],
        const SizedBox(height: AppSpacing.xl),
        // The quiet save state, right above the button whose meaning it has
        // replaced. Deliberately not a control — this sheet already has one
        // submit affordance and one destructive one.
        Align(
          alignment: Alignment.centerRight,
          child: AutosaveIndicator(status: _status),
        ),
        const SizedBox(height: AppSpacing.md),
        LzButton.primary(
          label: 'Save',
          icon: Icons.check_rounded,
          loading: _status == AutosaveStatus.saving,
          expand: true,
          onPressed: _busy ? null : _submit,
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

/// Raised when one of the caller-supplied writes reported failure. It exists so
/// [AutosaveController] can report [AutosaveStatus.failed] — a partial write
/// that quietly returned "saved" is exactly the lie auto-save must not tell.
class _ProjectWriteFailed implements Exception {
  const _ProjectWriteFailed();
  @override
  String toString() => 'A project write did not land';
}
