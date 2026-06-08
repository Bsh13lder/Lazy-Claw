import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

/// The ✨ AI-edit instruction box — mobile sibling of the web `DocAiPopover`.
///
/// Collects a natural-language instruction for the open document and returns it
/// (trimmed, non-empty) when the user taps "Apply", or `null` when cancelled.
/// The calling viewer runs the actual `POST /api/<kind>/<id>/ai` request so it
/// can reload the snapshot / switch to the new PDF id in place.
abstract final class DocAiBox {
  DocAiBox._();

  static Future<String?> show(
    BuildContext context, {
    String kindLabel = 'document',
  }) {
    return LzBottomSheet.show<String>(
      context,
      title: '✨ Ask AI to edit',
      builder: (_) => _AiForm(kindLabel: kindLabel),
    );
  }
}

/// A full-screen blocking overlay shown while an AI edit is in flight. Shared
/// by every document viewer so the ✨ flow looks identical across kinds.
class AiApplyingOverlay extends StatelessWidget {
  const AiApplyingOverlay({super.key, this.label = 'Applying AI edit…'});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Positioned.fill(
      child: ColoredBox(
        color: AppColors.bgBase.withValues(alpha: 0.6),
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const CircularProgressIndicator(color: AppColors.accent),
              const SizedBox(height: AppSpacing.lg),
              Text(label, style: AppText.body),
            ],
          ),
        ),
      ),
    );
  }
}

class _AiForm extends StatefulWidget {
  const _AiForm({required this.kindLabel});

  final String kindLabel;

  @override
  State<_AiForm> createState() => _AiFormState();
}

class _AiFormState extends State<_AiForm> {
  final _ctrl = TextEditingController();
  bool _canApply = false;

  @override
  void initState() {
    super.initState();
    _ctrl.addListener(() {
      final can = _ctrl.text.trim().isNotEmpty;
      if (can != _canApply) setState(() => _canApply = can);
    });
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  void _apply() {
    final text = _ctrl.text.trim();
    if (text.isEmpty) return;
    Navigator.of(context).pop(text);
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Describe the change in plain English. The AI specialist edits this '
          '${widget.kindLabel} for you.',
          style: AppText.body.copyWith(color: AppColors.textMuted),
        ),
        const SizedBox(height: AppSpacing.lg),
        LzTextField(
          controller: _ctrl,
          hint: 'e.g. add a total row, sign the last page…',
          minLines: 3,
          maxLines: 6,
          autofocus: true,
          keyboardType: TextInputType.multiline,
          textInputAction: TextInputAction.newline,
        ),
        const SizedBox(height: AppSpacing.xl),
        Row(
          children: [
            Expanded(
              child: LzButton.secondary(
                label: 'Cancel',
                onPressed: () => Navigator.of(context).pop(),
                expand: true,
              ),
            ),
            const SizedBox(width: AppSpacing.md),
            Expanded(
              child: LzButton.primary(
                label: 'Apply',
                icon: Icons.auto_awesome,
                onPressed: _canApply ? _apply : null,
                expand: true,
              ),
            ),
          ],
        ),
      ],
    );
  }
}
