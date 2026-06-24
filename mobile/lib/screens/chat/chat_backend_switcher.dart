/// Backend switcher for the chat app bar.
///
/// A compact, on-brand pill (styling mirrors [ModeSwitcher]'s `_ModePill`)
/// showing the current chat backend: **Server** (the LazyClaw agent over the
/// WebSocket) or one of the on-device local models. Tapping it opens a
/// token-styled bottom sheet listing the server plus every catalog model;
/// picking one flips the shared [chatBackendProvider].
///
/// When a local model is picked it is eagerly loaded via
/// [LocalAiController.selectAndLoad] (only if it isn't already the active,
/// ready model) so the chat can immediately show load progress / errors. The
/// pill also reflects the load phase with a tiny spinner.
///
/// This is purely a second entry point onto existing state — no new chat state,
/// no transport changes. It mirrors how [ModeSwitcher] re-uses
/// `agentModeProvider`.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons/lucide_icons.dart';

import '../../local_ai/local_ai_providers.dart';
import '../../local_ai/local_model.dart';
import '../../ui/ui.dart';
import 'chat_backend.dart';

/// The tappable chat-backend pill for the chat app bar.
class ChatBackendSwitcher extends ConsumerWidget {
  const ChatBackendSwitcher({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final backend = ref.watch(chatBackendProvider);
    final ai = ref.watch(localAiControllerProvider);

    final isLocal = backend != null;
    final label = isLocal
        ? (localModelById(backend)?.displayName ?? 'Local')
        : 'Server';
    final icon = isLocal ? LucideIcons.cpu : LucideIcons.cloud;

    // Show a spinner while the chosen local model is loading, so the pill
    // mirrors ModeSwitcher's "saving" affordance.
    final busy = isLocal &&
        ai.activeModelId == backend &&
        ai.phase == LocalAiPhase.loading;

    return _BackendPill(
      label: label,
      icon: icon,
      busy: busy,
      onTap: () => _openSheet(context, ref, backend),
    );
  }

  Future<void> _openSheet(
    BuildContext context,
    WidgetRef ref,
    String? current,
  ) async {
    // Sentinel `_kServerValue` represents the server option in the sheet,
    // since `null` can't round-trip through the pop result.
    final picked = await LzBottomSheet.show<String>(
      context,
      title: 'Chat backend',
      builder: (ctx) => _BackendSheet(current: current),
    );
    if (picked == null) return;

    final pickedModelId = picked == _kServerValue ? null : picked;
    if (pickedModelId == current) return;

    ref.read(chatBackendProvider.notifier).state = pickedModelId;

    // Eager-load a freshly-picked local model so the chat shows progress.
    if (pickedModelId != null) {
      final ai = ref.read(localAiControllerProvider);
      final needsLoad =
          ai.activeModelId != pickedModelId || ai.phase != LocalAiPhase.ready;
      if (needsLoad) {
        await ref
            .read(localAiControllerProvider.notifier)
            .selectAndLoad(pickedModelId);
      }
    }
  }
}

/// Sentinel value standing in for the "Server" row in the picker sheet.
const String _kServerValue = '__server__';

/// The compact pill rendered in the app-bar actions row. Mirrors the styling
/// of `_ModePill` in `mode_switcher.dart`.
class _BackendPill extends StatelessWidget {
  const _BackendPill({
    required this.label,
    required this.icon,
    required this.busy,
    required this.onTap,
  });

  final String label;
  final IconData icon;
  final bool busy;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      label: 'Chat backend: $label',
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
                if (busy)
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

/// The bottom-sheet body: a "Server" row plus one row per catalog model, with
/// the current selection check-marked.
class _BackendSheet extends StatelessWidget {
  const _BackendSheet({required this.current});

  /// `null` = server selected; otherwise the active local model id.
  final String? current;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _BackendRow(
          value: _kServerValue,
          icon: LucideIcons.cloud,
          label: 'Server',
          subtitle: 'LazyClaw agent — tools, tasks, channels',
          selected: current == null,
        ),
        for (final model in kLocalModels)
          _BackendRow(
            value: model.id,
            icon: LucideIcons.cpu,
            label: model.displayName,
            subtitle: model.sizeLabel,
            selected: model.id == current,
          ),
      ],
    );
  }
}

class _BackendRow extends StatelessWidget {
  const _BackendRow({
    required this.value,
    required this.icon,
    required this.label,
    required this.subtitle,
    required this.selected,
  });

  final String value;
  final IconData icon;
  final String label;
  final String subtitle;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    return LzListTile(
      title: label,
      subtitle: subtitle,
      leading: Icon(
        icon,
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
