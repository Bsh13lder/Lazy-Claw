/// Local AI — on-device model manager.
///
/// Lists the catalog models, shows whether each is present on the device (and
/// its size), and lets the user Load one into the engine, open a local Chat, or
/// Delete it. Models arrive primarily via USB push from the Mac (see the plan);
/// when one isn't on the device yet, the card explains how to get it there.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons/lucide_icons.dart';

import '../../local_ai/local_ai_providers.dart';
import '../../local_ai/local_model.dart';
import '../../local_ai/local_model_store.dart';
import '../../ui/ui.dart';
import 'local_chat_screen.dart';

class LocalModelsScreen extends ConsumerWidget {
  const LocalModelsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final statusesAsync = ref.watch(localModelStatusesProvider);
    final ai = ref.watch(localAiControllerProvider);

    return LzScaffold(
      title: 'Local AI',
      body: statusesAsync.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => LzErrorState(
          message: 'Could not read models: $e',
          onRetry: () => ref.invalidate(localModelStatusesProvider),
        ),
        data: (statuses) => RefreshIndicator(
          onRefresh: () async => ref.invalidate(localModelStatusesProvider),
          child: ListView(
            padding: const EdgeInsets.all(AppSpacing.md),
            children: [
              const _Intro(),
              const SizedBox(height: AppSpacing.md),
              if (ai.phase == LocalAiPhase.error && ai.error != null) ...[
                LzBanner(
                  message: ai.error!,
                  icon: LucideIcons.alertTriangle,
                ),
                const SizedBox(height: AppSpacing.md),
              ],
              for (final s in statuses) ...[
                _ModelCard(status: s, ai: ai),
                const SizedBox(height: AppSpacing.md),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _Intro extends StatelessWidget {
  const _Intro();
  @override
  Widget build(BuildContext context) {
    return Text(
      'Run an AI model entirely on this phone — no server, fully offline. '
      'Load a model, then open a local chat. One model runs at a time.',
      style: AppText.caption.copyWith(color: AppColors.textSecondary),
    );
  }
}

class _ModelCard extends ConsumerWidget {
  const _ModelCard({required this.status, required this.ai});

  final LocalModelStatus status;
  final LocalAiState ai;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final m = status.model;
    final isActive = ai.activeModelId == m.id;
    final isLoading = isActive && ai.phase == LocalAiPhase.loading;
    final isReady = isActive && ai.phase == LocalAiPhase.ready;

    return LzCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  m.displayName,
                  style: AppText.body.copyWith(fontWeight: FontWeight.w700),
                ),
              ),
              if (m.recommended)
                const LzChip(label: 'Recommended'),
            ],
          ),
          const SizedBox(height: AppSpacing.xs),
          Text(
            '${m.license} · ${m.sizeLabel}'
            '${status.present ? '' : ' · not on device'}',
            style: AppText.caption.copyWith(color: AppColors.textMuted),
          ),
          const SizedBox(height: AppSpacing.md),
          if (!status.present)
            _MissingActions(model: m)
          else if (!status.complete)
            Text(
              'Partial file (${(status.sizeBytes / (1024 * 1024)).round()} MB) — '
              're-push it over USB.',
              style: AppText.caption.copyWith(color: AppColors.warn),
            )
          else
            _ReadyActions(
              model: m,
              isLoading: isLoading,
              isReady: isReady,
            ),
        ],
      ),
    );
  }
}

class _MissingActions extends ConsumerWidget {
  const _MissingActions({required this.model});
  final LocalModel model;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final dl = ref.watch(localDownloadControllerProvider);
    final downloading =
        dl.isFor(model.id) && dl.phase == DownloadPhase.downloading;

    if (downloading) {
      final pct = (dl.fraction * 100).clamp(0, 100).toStringAsFixed(0);
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          LzProgressBar(value: dl.fraction.clamp(0.0, 1.0)),
          const SizedBox(height: AppSpacing.xs),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('Downloading… $pct%',
                  style: AppText.caption.copyWith(color: AppColors.textMuted)),
              GestureDetector(
                onTap: () =>
                    ref.read(localDownloadControllerProvider.notifier).cancel(),
                child: Text('Cancel',
                    style: AppText.caption.copyWith(color: AppColors.error)),
              ),
            ],
          ),
        ],
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (dl.isFor(model.id) && dl.phase == DownloadPhase.error) ...[
          Text('Download failed: ${dl.error}',
              style: AppText.caption.copyWith(color: AppColors.error)),
          const SizedBox(height: AppSpacing.sm),
        ],
        LzButton.secondary(
          label: 'Download on device (${model.sizeLabel})',
          icon: LucideIcons.download,
          expand: true,
          onPressed: () =>
              ref.read(localDownloadControllerProvider.notifier).start(model),
        ),
        const SizedBox(height: AppSpacing.sm),
        Text(
          'Or push it over USB (faster):\n'
          'adb push ${model.fileName} '
          '/sdcard/Android/data/com.lazyclaw.lazyclaw_mobile/files/models/',
          style: AppText.caption.copyWith(
            color: AppColors.textMuted,
            fontFamily: 'monospace',
          ),
        ),
      ],
    );
  }
}

class _ReadyActions extends ConsumerWidget {
  const _ReadyActions({
    required this.model,
    required this.isLoading,
    required this.isReady,
  });

  final LocalModel model;
  final bool isLoading;
  final bool isReady;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Row(
      children: [
        if (isReady)
          Expanded(
            child: LzButton(
              label: 'Chat',
              icon: LucideIcons.messageSquare,
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const LocalChatScreen()),
              ),
            ),
          )
        else
          Expanded(
            child: LzButton(
              label: isLoading ? 'Loading…' : 'Load',
              icon: LucideIcons.cpu,
              loading: isLoading,
              onPressed: isLoading
                  ? () {}
                  : () => ref
                      .read(localAiControllerProvider.notifier)
                      .selectAndLoad(model.id),
            ),
          ),
        const SizedBox(width: AppSpacing.sm),
        LzIconButton(
          icon: LucideIcons.trash2,
          onPressed: () async {
            await ref.read(localModelStoreProvider).delete(model);
            ref.invalidate(localModelStatusesProvider);
          },
        ),
      ],
    );
  }
}
