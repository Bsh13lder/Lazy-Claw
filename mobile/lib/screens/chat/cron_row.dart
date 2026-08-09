/// Compact centered system row for cron-kind user rows.
///
/// Scheduled-job instruction text ([JOB:...] / [WATCHER:...] /
/// [REMINDER...]) is persisted as a user-role chat row, but it is system
/// chatter — not something the user typed. Rendering it as a genuine
/// right-aligned user bubble reads as "I sent this". Instead: a muted
/// schedule pill with the job name, tappable to toggle the full instruction
/// text inline. Kit components + tokens only.
library;

import 'package:flutter/material.dart';
import '../../ui/ui.dart';

final RegExp _cronPrefixRe =
    RegExp(r'^\[(JOB|WATCHER|REMINDER)\s*:?\s*([^\]]*)\]');

/// Human label for a cron instruction row: the name inside the
/// `[JOB:name]` / `[WATCHER:name]` / `[REMINDER:name]` prefix, a readable
/// per-kind fallback when the prefix carries no name, else 'Scheduled job'.
String cronJobLabel(String content) {
  final t = content.trimLeft();
  final m = _cronPrefixRe.firstMatch(t);
  if (m != null) {
    final name = (m.group(2) ?? '').trim();
    if (name.isNotEmpty) return name;
    switch (m.group(1)) {
      case 'WATCHER':
        return 'Watcher';
      case 'REMINDER':
        return 'Reminder';
    }
    return 'Scheduled job';
  }
  // Unclosed / truncated prefixes still get a readable per-kind label.
  if (t.startsWith('[WATCHER')) return 'Watcher';
  if (t.startsWith('[REMINDER')) return 'Reminder';
  return 'Scheduled job';
}

class CronSystemRow extends StatefulWidget {
  const CronSystemRow({super.key, required this.content});

  /// The full instruction text (prefix included) — shown when expanded.
  final String content;

  @override
  State<CronSystemRow> createState() => _CronSystemRowState();
}

class _CronSystemRowState extends State<CronSystemRow> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: () => setState(() => _expanded = !_expanded),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Center(
              child: LzPill(
                icon: Icons.schedule,
                label: cronJobLabel(widget.content),
              ),
            ),
            if (_expanded) ...[
              const SizedBox(height: AppSpacing.xs),
              Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
                child: Text(
                  widget.content,
                  textAlign: TextAlign.center,
                  style: AppText.caption.copyWith(
                    color: AppColors.textSecondary,
                    fontWeight: FontWeight.w400,
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
