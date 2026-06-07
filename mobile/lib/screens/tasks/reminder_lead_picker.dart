import 'package:flutter/material.dart';

import '../../core/reminder_lead.dart';
import '../../ui/ui.dart';

/// A reminder lead-time picker: preset chips (None · At time · 10 min before ·
/// 30 min before · 1 hour before · 1 day before) plus a "Custom…" chip that
/// reveals a number field + unit selector (Min / Hr / Day).
///
/// Stateless w.r.t. the *chosen* lead — the parent owns [value] and is told of
/// changes via [onChanged]; only the transient custom-input text/unit live
/// here. Reuse it from both the add-task and edit-task sheets.
class ReminderLeadPicker extends StatefulWidget {
  const ReminderLeadPicker({
    super.key,
    required this.value,
    required this.onChanged,
  });

  /// The currently-selected lead (parent-owned).
  final ReminderLead value;

  /// Fired whenever the user picks a preset or edits the custom value/unit.
  final ValueChanged<ReminderLead> onChanged;

  @override
  State<ReminderLeadPicker> createState() => _ReminderLeadPickerState();
}

class _ReminderLeadPickerState extends State<ReminderLeadPicker> {
  late final TextEditingController _customController;
  ReminderLeadUnit _customUnit = ReminderLeadUnit.minutes;

  /// Whether the custom inputs are revealed. Sticky once opened so the user can
  /// tweak the number without the row collapsing.
  bool _customOpen = false;

  @override
  void initState() {
    super.initState();
    final v = widget.value;
    if (v.isCustom) {
      final parts = decomposeCustomLead(v.lead!);
      _customController = TextEditingController(text: parts.value.toString());
      _customUnit = parts.unit;
      _customOpen = true;
    } else {
      _customController = TextEditingController(text: '15');
    }
  }

  @override
  void dispose() {
    _customController.dispose();
    super.dispose();
  }

  void _emitCustom() {
    final n = int.tryParse(_customController.text.trim());
    if (n == null || n <= 0) return;
    widget.onChanged(ReminderLead(_customUnit.toDuration(n)));
  }

  @override
  Widget build(BuildContext context) {
    final value = widget.value;
    // The custom inputs show when the current value is a custom duration OR the
    // user explicitly opened the custom row (even before typing a valid value).
    final customSelected = value.isCustom || _customOpen;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: AppSpacing.sm,
          runSpacing: AppSpacing.sm,
          children: [
            for (final preset in ReminderLead.presets)
              LzChip(
                label: preset.label,
                icon: preset.isNone
                    ? Icons.notifications_off_outlined
                    : Icons.notifications_active_outlined,
                selected: !customSelected && value == preset,
                color: AppColors.accent,
                dense: true,
                onTap: () {
                  setState(() => _customOpen = false);
                  widget.onChanged(preset);
                },
              ),
            LzChip(
              label: 'Custom…',
              icon: Icons.tune_outlined,
              selected: customSelected,
              color: AppColors.accent,
              dense: true,
              onTap: () {
                setState(() => _customOpen = true);
                _emitCustom();
              },
            ),
          ],
        ),
        if (customSelected) ...[
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              SizedBox(
                width: 76,
                child: LzTextField(
                  controller: _customController,
                  fieldKey: const Key('reminder-custom-value'),
                  hint: '45',
                  keyboardType: TextInputType.number,
                  onChanged: (_) => _emitCustom(),
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              for (final unit in ReminderLeadUnit.values) ...[
                LzChip(
                  label: unit.label,
                  selected: _customUnit == unit,
                  color: AppColors.accent,
                  dense: true,
                  onTap: () {
                    setState(() => _customUnit = unit);
                    _emitCustom();
                  },
                ),
                const SizedBox(width: AppSpacing.xs),
              ],
            ],
          ),
          Padding(
            padding: const EdgeInsets.only(top: AppSpacing.xs),
            child: Text(
              'before the due time',
              style: AppText.caption.copyWith(color: AppColors.textMuted),
            ),
          ),
        ],
      ],
    );
  }
}
