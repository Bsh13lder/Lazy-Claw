import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:lazyclaw_mobile/core/recurrence.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

/// A compact "REPEAT" control: a wrap of choice chips — Does not repeat · Daily
/// · Weekdays · Weekly · Monthly · Yearly — plus a weekday sub-row that appears
/// when **Weekly** is selected so the user can pin a specific day.
///
/// Presentational + value-driven: it renders [value] and reports a fresh
/// [Recurrence] through [onChanged]. The host sheet owns the state and converts
/// the selection to a cron via [recurrenceToCron] on submit.
///
/// A [RecurrenceKind.custom] [value] (an existing task whose stored cron we
/// didn't author) renders as a single non-editable "Repeats" chip — tapping any
/// option replaces it with a known kind.
class RecurrencePicker extends StatelessWidget {
  const RecurrencePicker({
    super.key,
    required this.value,
    required this.onChanged,
    this.anchorWeekday,
  });

  /// The current selection. [RecurrenceKind.none] = does not repeat.
  final Recurrence value;

  /// Called with the new selection whenever the user taps a chip.
  final ValueChanged<Recurrence> onChanged;

  /// The weekday (Mon=1 … Sun=7) to seed a freshly-picked **Weekly** with — the
  /// composed due date's weekday, falling back to today. Lets "Weekly" default
  /// to a sensible concrete day without a due date being mandatory.
  final int? anchorWeekday;

  static const _options = <RecurrenceKind>[
    RecurrenceKind.none,
    RecurrenceKind.daily,
    RecurrenceKind.weekdays,
    RecurrenceKind.weekly,
    RecurrenceKind.monthly,
    RecurrenceKind.yearly,
  ];

  void _select(RecurrenceKind kind) {
    HapticFeedback.selectionClick();
    if (kind == RecurrenceKind.weekly) {
      // Seed the weekday from the existing weekly pick, else the anchor, else
      // today — so the cron always has a concrete day-of-week.
      final wd = value.kind == RecurrenceKind.weekly && value.weekday != null
          ? value.weekday
          : (anchorWeekday ?? DateTime.now().weekday);
      onChanged(Recurrence(RecurrenceKind.weekly, weekday: wd));
    } else {
      onChanged(Recurrence(kind));
    }
  }

  void _selectWeekday(int weekday) {
    HapticFeedback.selectionClick();
    onChanged(Recurrence(RecurrenceKind.weekly, weekday: weekday));
  }

  @override
  Widget build(BuildContext context) {
    final isWeekly = value.kind == RecurrenceKind.weekly;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: AppSpacing.sm,
          runSpacing: AppSpacing.sm,
          children: [
            for (final kind in _options)
              LzChip(
                key: ValueKey('recurrence-opt-${kind.name}'),
                label: _optionLabel(kind),
                icon: kind == RecurrenceKind.none
                    ? Icons.block_outlined
                    : Icons.repeat,
                selected: value.kind == kind,
                color: AppColors.accent,
                onTap: () => _select(kind),
              ),
            // A custom (non-authored) cron surfaces as an extra read-only chip
            // so the user sees the task DOES repeat, even though we can't map it
            // to a known option. Tapping a real option above replaces it.
            if (value.kind == RecurrenceKind.custom)
              LzChip(
                key: const ValueKey('recurrence-opt-custom'),
                label: recurrenceLabel(value),
                icon: Icons.repeat,
                selected: true,
                color: AppColors.info,
              ),
          ],
        ),
        // ── Weekly: which day? ────────────────────────────────────────────
        if (isWeekly) ...[
          const SizedBox(height: AppSpacing.sm),
          _WeekdayRow(
            selected: value.weekday ?? anchorWeekday ?? DateTime.now().weekday,
            onChanged: _selectWeekday,
          ),
        ],
      ],
    );
  }

  static String _optionLabel(RecurrenceKind kind) {
    switch (kind) {
      case RecurrenceKind.none:
        return 'No repeat';
      case RecurrenceKind.daily:
        return 'Daily';
      case RecurrenceKind.weekdays:
        return 'Weekdays';
      case RecurrenceKind.weekly:
        return 'Weekly';
      case RecurrenceKind.monthly:
        return 'Monthly';
      case RecurrenceKind.yearly:
        return 'Yearly';
      case RecurrenceKind.custom:
        return 'Repeats';
    }
  }
}

/// A row of seven single-letter weekday chips (Mon → Sun) for pinning the
/// weekly recurrence's day-of-week.
class _WeekdayRow extends StatelessWidget {
  const _WeekdayRow({required this.selected, required this.onChanged});

  /// The selected weekday (Mon=1 … Sun=7).
  final int selected;
  final ValueChanged<int> onChanged;

  static const _labels = <int, String>{
    DateTime.monday: 'M',
    DateTime.tuesday: 'T',
    DateTime.wednesday: 'W',
    DateTime.thursday: 'T',
    DateTime.friday: 'F',
    DateTime.saturday: 'S',
    DateTime.sunday: 'S',
  };

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: AppSpacing.xs,
      runSpacing: AppSpacing.xs,
      children: [
        for (final entry in _labels.entries)
          LzChip(
            key: ValueKey('recurrence-weekday-${entry.key}'),
            label: entry.value,
            selected: entry.key == selected,
            color: AppColors.accent,
            dense: true,
            onTap: () => onChanged(entry.key),
          ),
      ],
    );
  }
}
