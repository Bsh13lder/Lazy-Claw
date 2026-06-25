/// "Hey Lazy" — the on-device voice assistant screen.
///
/// Big mic orb: tap to talk, on-device speech-to-text → the local LLM → spoken
/// reply. Phase-driven controls (Done while listening, Stop while speaking).
/// Reuses the existing local-AI engine + model loader; nothing leaves the phone.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons/lucide_icons.dart';

import '../../assistant/lazy_assistant_controller.dart';
import '../../assistant/widgets/mic_state_indicator.dart';
import '../../assistant/widgets/provenance_badge.dart';
import '../../local_ai/local_ai_providers.dart';
import '../../ui/ui.dart';

class LazyAssistantScreen extends ConsumerWidget {
  const LazyAssistantScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(lazyAssistantProvider);
    final ctrl = ref.read(lazyAssistantProvider.notifier);
    final localAi = ref.watch(localAiControllerProvider);

    // First-cloud-hop consent: when a turn pauses for approval, present a
    // one-time sheet. Approve runs the held cloud turn; deny keeps it local.
    ref.listen<AssistantState>(lazyAssistantProvider, (prev, next) {
      final entered = prev?.phase != AssistantPhase.awaitingCloudConsent &&
          next.phase == AssistantPhase.awaitingCloudConsent;
      if (entered) {
        _showCloudConsentSheet(context, ctrl);
      }
    });

    return Scaffold(
      backgroundColor: AppColors.bgBase,
      appBar: AppBar(
        backgroundColor: AppColors.bgBase,
        elevation: 0,
        title: Row(
          children: [
            const Icon(LucideIcons.sparkles, color: AppColors.accent, size: 20),
            const SizedBox(width: AppSpacing.sm),
            Text('Hey Lazy', style: AppText.titleL),
          ],
        ),
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.lg),
          child: !localAi.isReady
              ? _NeedModel(localAi: localAi)
              : Column(
                  children: [
                    Expanded(child: _Conversation(state: state)),
                    const SizedBox(height: AppSpacing.lg),
                    _MicArea(state: state, ctrl: ctrl),
                  ],
                ),
        ),
      ),
    );
  }

  Future<void> _showCloudConsentSheet(
      BuildContext context, LazyAssistantController ctrl) async {
    final approved = await LzBottomSheet.show<bool>(
      context,
      title: 'Ask LazyClaw in the cloud?',
      builder: (ctx) => Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            'This needs the internet, your tools or fresh info, so I\'d send '
            'just this request to your LazyClaw server. Plain chat stays on '
            'your phone. You can change this anytime in Settings.',
            style: AppText.body.copyWith(color: AppColors.textSecondary),
          ),
          const SizedBox(height: AppSpacing.xl),
          LzButton.primary(
            label: 'Continue in the cloud',
            icon: LucideIcons.cloud,
            onPressed: () => Navigator.of(ctx).pop(true),
          ),
          const SizedBox(height: AppSpacing.sm),
          LzButton.secondary(
            label: 'Keep it on my phone',
            icon: LucideIcons.smartphone,
            onPressed: () => Navigator.of(ctx).pop(false),
          ),
        ],
      ),
    );
    if (approved == true) {
      await ctrl.approveCloudOnce();
    } else {
      await ctrl.denyCloud();
    }
  }
}

class _NeedModel extends ConsumerWidget {
  const _NeedModel({required this.localAi});
  final LocalAiState localAi;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final loading = localAi.phase == LocalAiPhase.loading;
    final hasModel = localAi.activeModelId != null;
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(LucideIcons.brainCircuit, color: AppColors.accent, size: 48),
          const SizedBox(height: AppSpacing.lg),
          Text('Lazy runs on-device', style: AppText.title, textAlign: TextAlign.center),
          const SizedBox(height: AppSpacing.sm),
          Text(
            hasModel
                ? 'Load your local model to start talking.'
                : 'Pick an on-device model in Settings → Local AI first.',
            style: AppText.body.copyWith(color: AppColors.textSecondary),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: AppSpacing.xl),
          if (hasModel)
            LzButton.primary(
              label: loading ? 'Loading…' : 'Load model',
              icon: loading ? null : LucideIcons.play,
              onPressed: loading
                  ? () {}
                  : () => ref
                      .read(localAiControllerProvider.notifier)
                      .selectAndLoad(localAi.activeModelId!),
            ),
          if (localAi.error != null) ...[
            const SizedBox(height: AppSpacing.md),
            Text(localAi.error!,
                style: AppText.caption.copyWith(color: AppColors.error),
                textAlign: TextAlign.center),
          ],
        ],
      ),
    );
  }
}

class _Conversation extends StatelessWidget {
  const _Conversation({required this.state});
  final AssistantState state;

  @override
  Widget build(BuildContext context) {
    final hasAnything = state.transcript.isNotEmpty || state.response.isNotEmpty;
    if (!hasAnything) {
      return Center(
        child: Text(
          'Tap the mic and say something —\ntry "what should I cook tonight?"',
          style: AppText.body.copyWith(color: AppColors.textMuted),
          textAlign: TextAlign.center,
        ),
      );
    }
    return SingleChildScrollView(
      reverse: true,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (state.transcript.isNotEmpty)
            _Bubble(
              text: state.transcript,
              align: CrossAxisAlignment.end,
              color: AppColors.bgSurfaceElevated,
              textColor: AppColors.textPrimary,
            ),
          if (state.response.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.md),
            if (state.source != null)
              Align(
                alignment: Alignment.centerLeft,
                child: Padding(
                  padding: const EdgeInsets.only(bottom: AppSpacing.xs),
                  child: ProvenanceBadge(source: state.source!),
                ),
              ),
            _Bubble(
              text: state.response,
              align: CrossAxisAlignment.start,
              color: AppColors.accent.withValues(alpha: 0.14),
              textColor: AppColors.textPrimary,
              border: AppColors.accent.withValues(alpha: 0.4),
            ),
          ],
          if (state.error != null) ...[
            const SizedBox(height: AppSpacing.md),
            Text(state.error!, style: AppText.caption.copyWith(color: AppColors.error)),
          ],
        ],
      ),
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({
    required this.text,
    required this.align,
    required this.color,
    required this.textColor,
    this.border,
  });
  final String text;
  final CrossAxisAlignment align;
  final Color color;
  final Color textColor;
  final Color? border;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: align,
      children: [
        Container(
          constraints: BoxConstraints(
              maxWidth: MediaQuery.of(context).size.width * 0.82),
          padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.lg, vertical: AppSpacing.md),
          decoration: BoxDecoration(
            color: color,
            borderRadius: BorderRadius.circular(18),
            border: border != null ? Border.all(color: border!) : null,
          ),
          child: Text(text, style: AppText.bodyL.copyWith(color: textColor)),
        ),
      ],
    );
  }
}

class _MicArea extends StatelessWidget {
  const _MicArea({required this.state, required this.ctrl});
  final AssistantState state;
  final LazyAssistantController ctrl;

  @override
  Widget build(BuildContext context) {
    final (label, sublabel) = switch (state.phase) {
      AssistantPhase.listening => ('Listening…', 'Tap Done when you finish'),
      AssistantPhase.thinking => ('Thinking…', 'Lazy is writing a reply'),
      AssistantPhase.speaking => ('Speaking…', 'Tap Stop to interrupt'),
      AssistantPhase.awaitingCloudConsent => ('One moment…', 'Confirm to continue'),
      AssistantPhase.error => ('Tap to try again', state.error ?? ''),
      AssistantPhase.idle => ('Tap to talk', 'Fully on-device'),
    };
    final listening = state.phase == AssistantPhase.listening;
    final speaking = state.phase == AssistantPhase.speaking;
    final thinking = state.phase == AssistantPhase.thinking;

    return Column(
      children: [
        Text(label, style: AppText.title.copyWith(color: AppColors.accent)),
        const SizedBox(height: AppSpacing.xs),
        Text(sublabel,
            style: AppText.caption.copyWith(color: AppColors.textMuted),
            textAlign: TextAlign.center),
        const SizedBox(height: AppSpacing.lg),
        GestureDetector(
          onTap: thinking ? null : () => ctrl.toggleListen(),
          child: Stack(
            clipBehavior: Clip.none,
            children: [
              Container(
                width: 92,
                height: 92,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: const LinearGradient(
                    colors: [AppColors.accentGradientStart, AppColors.accentGradientEnd],
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                  ),
                  boxShadow: [
                    BoxShadow(
                      color: AppColors.accent.withValues(alpha: listening ? 0.6 : 0.3),
                      blurRadius: listening ? 28 : 14,
                      spreadRadius: listening ? 4 : 0,
                    ),
                  ],
                ),
                child: Icon(
                  thinking
                      ? LucideIcons.loader
                      : listening
                          ? LucideIcons.square
                          : LucideIcons.mic,
                  color: AppColors.onAccent,
                  size: 34,
                ),
              ),
              Positioned(
                top: 2,
                right: 2,
                child: MicStateIndicator(live: listening),
              ),
            ],
          ),
        ),
        const SizedBox(height: AppSpacing.lg),
        if (listening)
          LzButton.primary(label: 'Done', icon: LucideIcons.check, onPressed: ctrl.finishListening)
        else if (speaking)
          LzButton.secondary(label: 'Stop', icon: LucideIcons.square, onPressed: ctrl.stopSpeaking),
      ],
    );
  }
}
