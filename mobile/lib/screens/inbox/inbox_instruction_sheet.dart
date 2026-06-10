// Bottom sheet for editing a channel's standing instruction.
//
// "When something arrives on WhatsApp, do X" — saved server-side on the
// channel's watcher; the agent executes it as a real turn on every new
// message batch and reports the result through notifications, so execution
// is always visible.
//
// Kit constraints: LzTextField(hint:), LzButton(onPressed: null → disabled),
// tokens only.

library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../comms/inbox_providers.dart';
import '../../ui/ui.dart';

/// Opens the editor for [channel] ('whatsapp' | 'email' | 'instagram').
Future<void> showChannelInstructionSheet(
  BuildContext context,
  String channel,
) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: AppColors.bgSurfaceElevated,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(AppRadii.lg)),
    ),
    builder: (_) => _InstructionSheet(channel: channel),
  );
}

class _InstructionSheet extends ConsumerStatefulWidget {
  const _InstructionSheet({required this.channel});

  final String channel;

  @override
  ConsumerState<_InstructionSheet> createState() => _InstructionSheetState();
}

class _InstructionSheetState extends ConsumerState<_InstructionSheet> {
  final _controller = TextEditingController();
  bool _loading = true;
  bool _saving = false;
  bool _hadInstruction = false;

  @override
  void initState() {
    super.initState();
    Future.microtask(_load);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final current = await ref
          .read(inboxRepositoryProvider)
          .getChannelInstruction(widget.channel);
      if (!mounted) return;
      setState(() {
        _controller.text = current ?? '';
        _hadInstruction = current != null && current.isNotEmpty;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _loading = false);
    }
  }

  Future<void> _save() async {
    final text = _controller.text.trim();
    if (_saving) return;
    setState(() => _saving = true);
    try {
      final repo = ref.read(inboxRepositoryProvider);
      if (text.isEmpty) {
        await repo.clearChannelInstruction(widget.channel);
      } else {
        await repo.setChannelInstruction(widget.channel, text);
      }
      if (!mounted) return;
      Navigator.of(context).pop();
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(text.isEmpty
              ? 'Standing instruction cleared.'
              : 'Saved — I\'ll run this on every new '
                  '${widget.channel} message and report back.'),
          duration: const Duration(seconds: 3),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() => _saving = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Could not save: $e')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final bottomInset = MediaQuery.of(context).viewInsets.bottom;
    final channelLabel =
        widget.channel[0].toUpperCase() + widget.channel.substring(1);

    return Padding(
      padding: EdgeInsets.only(bottom: bottomInset),
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.lg,
            AppSpacing.lg,
            AppSpacing.lg,
            AppSpacing.lg,
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  const Icon(Icons.bolt_rounded, color: AppColors.accent),
                  AppSpacing.hGap(AppSpacing.sm),
                  Text(
                    '$channelLabel auto-pilot',
                    style: AppText.title.copyWith(color: AppColors.textPrimary),
                  ),
                ],
              ),
              AppSpacing.vGap(AppSpacing.sm),
              Text(
                'Standing instruction — the agent runs it on every new '
                '$channelLabel message and reports what it did. '
                'Leave empty and save to turn it off.',
                style: AppText.caption.copyWith(color: AppColors.textSecondary),
              ),
              AppSpacing.vGap(AppSpacing.md),
              if (_loading)
                const Center(
                  child: Padding(
                    padding: EdgeInsets.all(AppSpacing.lg),
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: AppColors.accent,
                    ),
                  ),
                )
              else ...[
                LzTextField(
                  controller: _controller,
                  hint: 'e.g. "Summarize new messages and flag anything '
                      'urgent" or "If it\'s a pricing question, reply with '
                      'our standard rates"',
                  maxLines: 5,
                  minLines: 3,
                  keyboardType: TextInputType.multiline,
                ),
                AppSpacing.vGap(AppSpacing.md),
                Row(
                  mainAxisAlignment: MainAxisAlignment.end,
                  children: [
                    if (_hadInstruction)
                      LzButton(
                        label: 'Clear',
                        variant: LzButtonVariant.ghost,
                        onPressed: _saving
                            ? null
                            : () {
                                _controller.clear();
                                _save();
                              },
                      ),
                    AppSpacing.hGap(AppSpacing.sm),
                    LzButton(
                      label: 'Save',
                      variant: LzButtonVariant.primary,
                      loading: _saving,
                      onPressed: _saving ? null : _save,
                    ),
                  ],
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
