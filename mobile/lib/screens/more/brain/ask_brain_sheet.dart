import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';

import '../../../repositories/lazybrain_repository.dart';
import '../../../ui/ui.dart';
import 'brain_markdown.dart';

/// Open the "Ask Brain" bottom sheet. [ask] is the one-shot RAG call
/// (BrainNotifier.ask) — passed in so the sheet stays provider-agnostic.
Future<void> showAskBrainSheet(
  BuildContext context, {
  required Future<AskResult> Function(String question) ask,
}) {
  return LzBottomSheet.show<void>(
    context,
    title: 'Ask Brain',
    builder: (ctx) => _AskBrainSheet(ask: ask),
  );
}

class _AskBrainSheet extends StatefulWidget {
  const _AskBrainSheet({required this.ask});

  final Future<AskResult> Function(String question) ask;

  @override
  State<_AskBrainSheet> createState() => _AskBrainSheetState();
}

class _AskBrainSheetState extends State<_AskBrainSheet> {
  final _questionCtrl = TextEditingController();
  bool _loading = false;
  AskResult? _result;
  String? _error;

  @override
  void dispose() {
    _questionCtrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final question = _questionCtrl.text.trim();
    if (question.isEmpty || _loading) return;
    setState(() {
      _loading = true;
      _error = null;
      _result = null;
    });
    try {
      final result = await widget.ask(question);
      if (!mounted) return;
      setState(() {
        _result = result;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: [
          LzTextField(
            controller: _questionCtrl,
            hint: 'Ask anything your notes might know…',
            prefixIcon: Icons.psychology_outlined,
            minLines: 1,
            maxLines: 3,
            autofocus: true,
            textInputAction: TextInputAction.send,
            onSubmitted: (_) => _submit(),
          ),
          const SizedBox(height: AppSpacing.md),
          LzButton.primary(
            label: 'Ask',
            icon: Icons.auto_awesome_outlined,
            loading: _loading,
            expand: true,
            onPressed: _loading ? null : _submit,
          ),
          if (_error != null) ...[
            const SizedBox(height: AppSpacing.lg),
            Text(
              _error!,
              style: AppText.body.copyWith(color: AppColors.error),
            ),
          ],
          if (_result != null) ...[
            const SizedBox(height: AppSpacing.lg),
            _AnswerView(result: _result!),
          ],
          const SizedBox(height: AppSpacing.md),
        ],
      ),
    );
  }
}

// ── Rendered answer ─────────────────────────────────────────────────────────

class _AnswerView extends StatelessWidget {
  const _AnswerView({required this.result});

  final AskResult result;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        MarkdownBody(
          data: result.answer.isNotEmpty
              ? result.answer
              : '_The brain came back empty._',
          selectable: true,
          styleSheet: brainMarkdownStyle(),
        ),
        if (result.sources.isNotEmpty) ...[
          const SizedBox(height: AppSpacing.lg),
          Text(
            'SOURCES (${result.sourceCount})',
            style: AppText.caption.copyWith(
              color: AppColors.textMuted,
              letterSpacing: 0.8,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          Wrap(
            spacing: AppSpacing.xs,
            runSpacing: AppSpacing.xs,
            children: result.sources
                .map((s) => LzChip(label: s, dense: true))
                .toList(),
          ),
        ],
      ],
    );
  }
}
