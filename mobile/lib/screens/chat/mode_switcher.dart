/// Operating-mode switcher for the chat app bar.
///
/// A compact, on-brand pill showing the current operating mode
/// ([kAgentModeLabels]: Ask / Plan / Action / Execute). Tapping it opens a
/// token-styled bottom sheet listing the four modes; picking one persists via
/// the shared [agentModeProvider] and confirms with a SnackBar (or surfaces the
/// error on failure).
///
/// Reuses the existing [agentModeProvider] / [AgentModeNotifier] — the same
/// shared state the Settings screen's operating-mode section drives — so the
/// chip and Settings stay in lock-step. No new provider, no new repository
/// method: this is purely a second entry point onto existing state.
///
/// The chip never blocks the chat: while the mode is loading it shows the
/// default label, and while saving it shows a tiny spinner instead of the icon.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../providers/settings_repo_provider.dart';
import '../../repositories/settings_repository.dart';
import '../../ui/ui.dart';

/// Maps each operating-mode value to a small leading icon for the pill + menu.
const Map<String, IconData> _kModeIcons = {
  'chat': Icons.chat_bubble_outline_rounded, // Ask
  'plan': Icons.map_outlined, // Plan
  'ask': Icons.bolt_outlined, // Action
  'auto': Icons.auto_awesome_outlined, // Execute
};

/// One-line description of each mode, shown as the sheet row subtitle.
const Map<String, String> _kModeHints = {
  'chat': 'Talk only — no tools run',
  'plan': 'Research first, then gate before acting',
  'ask': 'Confirm each write (default)',
  'auto': 'Autonomous — acts without asking',
};

IconData _iconFor(String mode) =>
    _kModeIcons[mode] ?? Icons.tune_rounded;

String _labelFor(String mode) =>
    kAgentModeLabels[mode] ?? kAgentModeLabels[kDefaultAgentMode]!;

/// The tappable operating-mode pill for the chat app bar.
class ModeSwitcher extends ConsumerWidget {
  const ModeSwitcher({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final modeState = ref.watch(agentModeProvider);
    // Show the last-known mode while loading; never block the chat on it.
    final current = modeState.value.maybeWhen(
      data: (m) => m,
      orElse: () => kDefaultAgentMode,
    );
    final saving = modeState.saving;

    return _ModePill(
      label: _labelFor(current),
      icon: _iconFor(current),
      saving: saving,
      onTap: saving ? null : () => _openSheet(context, ref, current),
    );
  }

  Future<void> _openSheet(
    BuildContext context,
    WidgetRef ref,
    String current,
  ) async {
    final picked = await LzBottomSheet.show<String>(
      context,
      title: 'Operating mode',
      builder: (ctx) => _ModeSheet(current: current),
    );
    if (picked == null || picked == current) return;

    final err = await ref.read(agentModeProvider.notifier).setMode(picked);
    if (!context.mounted) return;

    final messenger = ScaffoldMessenger.of(context);
    messenger.hideCurrentSnackBar();
    if (err == null) {
      messenger.showSnackBar(
        SnackBar(content: Text('Mode set to ${_labelFor(picked)}')),
      );
    } else {
      messenger.showSnackBar(
        SnackBar(content: Text('Could not change mode: $err')),
      );
    }
  }
}

/// The compact pill rendered in the app-bar actions row.
class _ModePill extends StatelessWidget {
  const _ModePill({
    required this.label,
    required this.icon,
    required this.saving,
    required this.onTap,
  });

  final String label;
  final IconData icon;
  final bool saving;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      label: 'Operating mode: $label',
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onTap,
          borderRadius: AppRadii.rPill,
          splashColor: AppColors.accent.withValues(alpha: 0.08),
          child: Container(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.md,
              vertical: AppSpacing.xs + 1,
            ),
            decoration: BoxDecoration(
              color: AppColors.bgSurfaceElevated,
              borderRadius: AppRadii.rPill,
              border: Border.all(color: AppColors.borderDefault),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (saving)
                  const SizedBox(
                    width: 14,
                    height: 14,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      valueColor:
                          AlwaysStoppedAnimation<Color>(AppColors.accent),
                    ),
                  )
                else
                  Icon(icon, size: 14, color: AppColors.accent),
                const SizedBox(width: AppSpacing.xs + 2),
                Text(
                  label,
                  style: AppText.caption.copyWith(
                    color: AppColors.textPrimary,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(width: AppSpacing.xs),
                const Icon(
                  Icons.keyboard_arrow_down_rounded,
                  size: 16,
                  color: AppColors.textMuted,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// The bottom-sheet body listing the four modes with the current one selected.
class _ModeSheet extends StatelessWidget {
  const _ModeSheet({required this.current});

  final String current;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final entry in kAgentModeLabels.entries)
          _ModeRow(
            value: entry.key,
            label: entry.value,
            selected: entry.key == current,
          ),
      ],
    );
  }
}

class _ModeRow extends StatelessWidget {
  const _ModeRow({
    required this.value,
    required this.label,
    required this.selected,
  });

  final String value;
  final String label;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    return LzListTile(
      title: label,
      subtitle: _kModeHints[value],
      leading: Icon(
        _iconFor(value),
        size: 20,
        color: selected ? AppColors.accent : AppColors.textSecondary,
      ),
      titleStyle: AppText.body.copyWith(
        color: selected ? AppColors.accent : AppColors.textPrimary,
        fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
      ),
      trailing: selected
          ? const Icon(
              Icons.check_rounded,
              size: 18,
              color: AppColors.accent,
            )
          : null,
      onTap: () => Navigator.of(context).pop(value),
    );
  }
}
