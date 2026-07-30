import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

/// The shared "Start date / Due date" chip pair for the project sheets
/// (add + edit) — mirrors how [ProjectColorSwatches] is shared between the two.
///
/// Each chip opens the app's standard date picker and shows the chosen
/// `yyyy-MM-dd` day; a ✕ beside a set chip clears it back to unset (null).
/// Purely presentational: the host sheet owns the state and decides how a null
/// maps to the wire (absent on create, the `''` clear sentinel on update).
class ProjectDateChips extends StatelessWidget {
  const ProjectDateChips({
    super.key,
    required this.startDate,
    required this.dueDate,
    required this.onStartChanged,
    required this.onDueChanged,
    this.enabled = true,
  });

  /// The selected days (`yyyy-MM-dd`), or null when unset.
  final String? startDate;
  final String? dueDate;

  final ValueChanged<String?> onStartChanged;
  final ValueChanged<String?> onDueChanged;

  /// Disables both chips (e.g. while the sheet is saving/deleting).
  final bool enabled;

  Future<void> _pick(
    BuildContext context,
    String? current,
    ValueChanged<String?> onChanged,
  ) async {
    final now = DateTime.now();
    final existing = current == null ? null : DateTime.tryParse(current);
    final picked = await showDatePicker(
      context: context,
      initialDate: existing ?? now,
      firstDate: now.subtract(const Duration(days: 365)),
      lastDate: now.add(const Duration(days: 3650)),
      builder: (ctx, child) => Theme(
        data: Theme.of(ctx).copyWith(
          colorScheme: ColorScheme.dark(
            primary: AppColors.accent,
            surface: AppColors.bgSurfaceElevated,
          ),
        ),
        child: child!,
      ),
    );
    if (picked != null) onChanged(_iso(picked));
  }

  static String _iso(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-'
      '${d.month.toString().padLeft(2, '0')}-'
      '${d.day.toString().padLeft(2, '0')}';

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: AppSpacing.sm,
      runSpacing: AppSpacing.sm,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        LzChip(
          key: const Key('project-start-date'),
          label: startDate ?? 'Start date',
          icon: Icons.today_outlined,
          selected: startDate != null,
          color: AppColors.accent,
          onTap: enabled
              ? () => _pick(context, startDate, onStartChanged)
              : null,
        ),
        if (startDate != null)
          GestureDetector(
            key: const Key('project-start-date-clear'),
            onTap: enabled ? () => onStartChanged(null) : null,
            child: Icon(Icons.close, size: 16, color: AppColors.textMuted),
          ),
        LzChip(
          key: const Key('project-due-date'),
          label: dueDate ?? 'Due date',
          icon: Icons.event_outlined,
          selected: dueDate != null,
          color: AppColors.info,
          onTap: enabled ? () => _pick(context, dueDate, onDueChanged) : null,
        ),
        if (dueDate != null)
          GestureDetector(
            key: const Key('project-due-date-clear'),
            onTap: enabled ? () => onDueChanged(null) : null,
            child: Icon(Icons.close, size: 16, color: AppColors.textMuted),
          ),
      ],
    );
  }
}
