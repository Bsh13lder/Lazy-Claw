/// Inbox bulk-select UI: the Ledger's bulk-action bar, the bulk "Assign to
/// project" sheet, and the Auto-assign preview sheet.
library;

import 'package:flutter/material.dart';

import '../../models/inbox_suggestion.dart';
import '../../models/project.dart';
import '../../models/task.dart';
import '../../models/task_project_link.dart';
import '../../ui/ui.dart';

/// The Ledger's inbox bulk-action bar: a selected count, Assign/Auto actions,
/// and a cancel — all gated on the SAME [busy] flag (the shared bulk-mutation
/// lock in `_LedgerTabState`) so every entry point disables together while any
/// one bulk operation is in flight.
class BulkActionBar extends StatelessWidget {
  const BulkActionBar({
    super.key,
    required this.count,
    required this.busy,
    required this.onAssign,
    required this.onAuto,
    required this.onCancel,
  });

  final int count;
  final bool busy;
  final VoidCallback? onAssign;
  final VoidCallback? onAuto;
  final VoidCallback onCancel;

  @override
  Widget build(BuildContext context) {
    return LzCard(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.sm,
      ),
      child: Row(
        children: [
          if (busy) ...[
            const SizedBox(
              width: 14,
              height: 14,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                color: AppColors.accent,
              ),
            ),
            const SizedBox(width: AppSpacing.sm),
          ],
          Text(
            '$count selected',
            style: AppText.label.copyWith(
              color: AppColors.textPrimary,
              fontWeight: FontWeight.w700,
            ),
          ),
          const Spacer(),
          _BarAction(label: 'Assign', onTap: busy ? null : onAssign),
          const SizedBox(width: AppSpacing.md),
          _BarAction(label: '✨ Auto', onTap: busy ? null : onAuto),
          const SizedBox(width: AppSpacing.md),
          LzIconButton(
            icon: Icons.close_rounded,
            tooltip: 'Cancel selection',
            size: 18,
            color: AppColors.textMuted,
            onPressed: busy ? null : onCancel,
          ),
        ],
      ),
    );
  }
}

/// A small text action used inside [BulkActionBar]. Dims and stops
/// responding to taps when [onTap] is null (busy or not yet actionable).
class _BarAction extends StatelessWidget {
  const _BarAction({required this.label, required this.onTap});

  final String label;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final enabled = onTap != null;
    return InkWell(
      onTap: onTap,
      borderRadius: AppRadii.rMd,
      child: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.sm,
          vertical: AppSpacing.xs,
        ),
        child: Text(
          label,
          style: AppText.label.copyWith(
            color: enabled ? AppColors.accent : AppColors.textMuted,
            fontWeight: FontWeight.w700,
          ),
        ),
      ),
    );
  }
}

/// The bulk "Assign to project" sheet: a project picker (inbox already
/// excluded by the caller) + an optional task picker scoped to the chosen
/// project — mirrors `_ProjectPicker`/`_TaskPicker`'s dropdown idiom in
/// `expense_detail_sheet.dart`. Pops `(projectId, taskId)` on Assign.
class BulkAssignSheet extends StatefulWidget {
  const BulkAssignSheet({
    super.key,
    required this.projects,
    required this.allTasks,
  });

  /// Assignable projects — the inbox project is already excluded.
  final List<Project> projects;
  final List<Task> allTasks;

  @override
  State<BulkAssignSheet> createState() => _BulkAssignSheetState();
}

class _BulkAssignSheetState extends State<BulkAssignSheet> {
  String? _projectId;
  String? _taskId;

  @override
  void initState() {
    super.initState();
    _projectId = widget.projects.isNotEmpty ? widget.projects.first.id : null;
  }

  /// Tasks selectable for the currently-picked project — empty when nothing
  /// is picked (shouldn't happen once a project list is non-empty, but stays
  /// safe if it ever is).
  List<Task> get _availableTasks {
    for (final p in widget.projects) {
      if (p.id == _projectId) return tasksForProject(widget.allTasks, p);
    }
    return const [];
  }

  @override
  Widget build(BuildContext context) {
    final tasks = _availableTasks;
    final hasTaskSelected = tasks.any((t) => t.id == _taskId);

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text('Project', style: AppText.label.copyWith(color: AppColors.textSecondary)),
        const SizedBox(height: AppSpacing.sm),
        Container(
          decoration: BoxDecoration(
            color: AppColors.bgSurfaceElevated,
            borderRadius: AppRadii.rMd,
            border: Border.all(color: AppColors.borderDefault),
          ),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String>(
              key: const Key('bulk-assign-project'),
              value: widget.projects.any((p) => p.id == _projectId)
                  ? _projectId
                  : null,
              isExpanded: true,
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
              dropdownColor: AppColors.bgSurfaceElevated,
              style: AppText.body,
              icon: const Icon(Icons.keyboard_arrow_down_rounded,
                  color: AppColors.textMuted),
              items: [
                for (final p in widget.projects)
                  DropdownMenuItem<String>(
                    value: p.id,
                    child: Text(p.name, style: AppText.body),
                  ),
              ],
              onChanged: (id) => setState(() {
                _projectId = id;
                // A task belongs to one project — reset it on project change,
                // same as the single-expense detail sheet.
                _taskId = null;
              }),
            ),
          ),
        ),
        const SizedBox(height: AppSpacing.lg),
        Text('Task (optional)',
            style: AppText.label.copyWith(color: AppColors.textSecondary)),
        const SizedBox(height: AppSpacing.sm),
        Container(
          decoration: BoxDecoration(
            color: AppColors.bgSurfaceElevated,
            borderRadius: AppRadii.rMd,
            border: Border.all(color: AppColors.borderDefault),
          ),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String>(
              key: const Key('bulk-assign-task'),
              value: hasTaskSelected ? _taskId : null,
              isExpanded: true,
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
              dropdownColor: AppColors.bgSurfaceElevated,
              style: AppText.body,
              icon: const Icon(Icons.keyboard_arrow_down_rounded,
                  color: AppColors.textMuted),
              items: [
                DropdownMenuItem<String>(
                  value: null,
                  child: Text(
                    '(no task)',
                    style: AppText.body.copyWith(color: AppColors.textMuted),
                  ),
                ),
                for (final t in tasks)
                  DropdownMenuItem<String>(
                    value: t.id,
                    child: Text(t.title,
                        style: AppText.body, overflow: TextOverflow.ellipsis),
                  ),
              ],
              onChanged: (id) => setState(() => _taskId = id),
            ),
          ),
        ),
        const SizedBox(height: AppSpacing.xl),
        LzButton.primary(
          key: const Key('bulk-assign-submit'),
          label: 'Assign',
          icon: Icons.check,
          expand: true,
          onPressed: _projectId == null
              ? null
              : () => Navigator.of(context).pop((_projectId!, _taskId)),
        ),
      ],
    );
  }
}

/// The Auto-assign preview sheet: lists every suggestion the server returned
/// (`description → projectName (confidence)`, "no match" rows dimmed), plus
/// the skipped-count hint, plus an "Apply N confident" button that pops
/// `true` to tell the caller to run the apply loop.
class AutoPreviewSheet extends StatelessWidget {
  const AutoPreviewSheet({
    super.key,
    required this.suggestions,
    required this.skipped,
    required this.labelFor,
  });

  final List<InboxSuggestion> suggestions;
  final int skipped;

  /// Resolves an expense id to a display label (description/vendor/amount).
  final String Function(String expenseId) labelFor;

  @override
  Widget build(BuildContext context) {
    final confident = confidentSuggestions(suggestions);

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (suggestions.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: AppSpacing.lg),
            child: Text(
              'No suggestions yet — try again once the inbox has some context to match on.',
              style: AppText.body.copyWith(color: AppColors.textMuted),
            ),
          )
        else
          ConstrainedBox(
            constraints: BoxConstraints(
              maxHeight: MediaQuery.of(context).size.height * 0.4,
            ),
            child: ListView.separated(
              shrinkWrap: true,
              itemCount: suggestions.length,
              separatorBuilder: (_, _) =>
                  Divider(height: 1, color: AppColors.borderSubtle),
              itemBuilder: (_, i) {
                final s = suggestions[i];
                final matched = s.projectId != null;
                final text = matched
                    ? '${labelFor(s.expenseId)} → ${s.projectName ?? "?"} (${s.confidence})'
                    : '${labelFor(s.expenseId)} — no match';
                return Opacity(
                  opacity: matched ? 1.0 : 0.5,
                  child: Padding(
                    padding:
                        const EdgeInsets.symmetric(vertical: AppSpacing.sm),
                    child: Text(text, style: AppText.body),
                  ),
                );
              },
            ),
          ),
        if (skipped > 0) ...[
          const SizedBox(height: AppSpacing.sm),
          Text(
            '$skipped more not analyzed — run again.',
            style: AppText.caption.copyWith(color: AppColors.textMuted),
          ),
        ],
        const SizedBox(height: AppSpacing.lg),
        LzButton.primary(
          key: const Key('auto-preview-apply'),
          label: confident.isEmpty
              ? 'No confident matches'
              : 'Apply ${confident.length} confident',
          icon: Icons.check,
          expand: true,
          onPressed:
              confident.isEmpty ? null : () => Navigator.of(context).pop(true),
        ),
      ],
    );
  }
}
