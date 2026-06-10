// Bottom sheet for editing standing instructions.
//
// Two scopes share one editor:
//   * CHANNEL  — "when something arrives on WhatsApp, do X" (saved on the
//     channel's watcher; runs on every new message batch).
//   * CONTACT  — "for THIS person, do X" (saved on the thread; runs whenever
//     that contact writes — layered over the channel-wide instruction).
//
// Both execute as real agent turns and report through notifications, so the
// user always sees what ran.
//
// Kit constraints: LzTextField(hint:), LzButton(onPressed: null → disabled),
// tokens only.

library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../comms/inbox_providers.dart';
import '../../ui/ui.dart';

/// Opens the CHANNEL-wide editor ('whatsapp' | 'email' | 'instagram').
Future<void> showChannelInstructionSheet(
  BuildContext context,
  String channel,
) {
  final channelLabel = channel[0].toUpperCase() + channel.substring(1);
  return _show(
    context,
    _SheetSpec(
      title: '$channelLabel auto-pilot',
      description: 'Standing instruction — the agent runs it on every new '
          '$channelLabel message and reports what it did. '
          'Leave empty and save to turn it off.',
      hint: 'e.g. "Summarize new messages and flag anything urgent" or '
          '"If it\'s a pricing question, reply with our standard rates"',
      savedMessage: "Saved — I'll run this on every new "
          '$channel message and report back.',
      load: (repo) => repo.getChannelInstruction(channel),
      save: (repo, text) => repo.setChannelInstruction(channel, text),
      clear: (repo) => repo.clearChannelInstruction(channel),
    ),
  );
}

/// Opens the PER-CONTACT editor for one thread.
Future<void> showThreadInstructionSheet(
  BuildContext context, {
  required String threadId,
  required String contactLabel,
}) {
  return _show(
    context,
    _SheetSpec(
      title: '$contactLabel auto-pilot',
      description: 'Standing instruction for THIS contact — the agent runs '
          'it whenever $contactLabel writes and reports what it did. '
          'Leave empty and save to turn it off.',
      hint: 'e.g. "Always answer in Spanish" or "If they ask about the '
          'invoice, send the latest PDF and let me know"',
      savedMessage: "Saved — I'll run this whenever "
          '$contactLabel writes and report back.',
      load: (repo) => repo.getThreadInstruction(threadId),
      save: (repo, text) => repo.setThreadInstruction(threadId, text),
      clear: (repo) => repo.clearThreadInstruction(threadId),
    ),
  );
}

Future<void> _show(BuildContext context, _SheetSpec spec) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: AppColors.bgSurfaceElevated,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(AppRadii.lg)),
    ),
    builder: (_) => _InstructionSheet(spec: spec),
  );
}

/// Everything scope-specific, so the sheet body stays scope-agnostic.
class _SheetSpec {
  const _SheetSpec({
    required this.title,
    required this.description,
    required this.hint,
    required this.savedMessage,
    required this.load,
    required this.save,
    required this.clear,
  });

  final String title;
  final String description;
  final String hint;
  final String savedMessage;
  final Future<String?> Function(dynamic repo) load;
  final Future<dynamic> Function(dynamic repo, String text) save;
  final Future<void> Function(dynamic repo) clear;
}

class _InstructionSheet extends ConsumerStatefulWidget {
  const _InstructionSheet({required this.spec});

  final _SheetSpec spec;

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
      final current =
          await widget.spec.load(ref.read(inboxRepositoryProvider));
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
        await widget.spec.clear(repo);
      } else {
        await widget.spec.save(repo, text);
      }
      if (!mounted) return;
      Navigator.of(context).pop();
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(text.isEmpty
              ? 'Standing instruction cleared.'
              : widget.spec.savedMessage),
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
                  Expanded(
                    child: Text(
                      widget.spec.title,
                      style:
                          AppText.title.copyWith(color: AppColors.textPrimary),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
              AppSpacing.vGap(AppSpacing.sm),
              Text(
                widget.spec.description,
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
                  hint: widget.spec.hint,
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
