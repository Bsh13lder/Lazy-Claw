import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';

import '../../../ui/ui.dart';

/// Shared token-driven markdown stylesheet for Brain surfaces (journal day
/// sheet, Ask Brain answers). Mirrors the NoteDetailScreen reader styling so
/// LazyBrain content renders identically everywhere.
MarkdownStyleSheet brainMarkdownStyle() {
  return MarkdownStyleSheet(
    p: AppText.bodyL,
    h1: AppText.headline,
    h2: AppText.titleL,
    h3: AppText.title,
    code: AppText.body.copyWith(
      fontFamily: 'monospace',
      backgroundColor: AppColors.bgSurfaceElevated,
      color: AppColors.accent,
    ),
    codeblockDecoration: BoxDecoration(
      color: AppColors.bgSurfaceElevated,
      borderRadius: AppRadii.rMd,
      border: Border.all(color: AppColors.borderSubtle),
    ),
    blockquoteDecoration: BoxDecoration(
      border: Border(
        left: BorderSide(
          color: AppColors.accent.withValues(alpha: 0.6),
          width: 3,
        ),
      ),
      color: AppColors.bgSurface,
    ),
    blockquote: AppText.body.copyWith(
      color: AppColors.textSecondary,
      fontStyle: FontStyle.italic,
    ),
    a: AppText.body.copyWith(
      color: AppColors.accent,
      decoration: TextDecoration.underline,
      decorationColor: AppColors.accent,
    ),
    listBullet: AppText.body,
    horizontalRuleDecoration: const BoxDecoration(
      border: Border(
        top: BorderSide(color: AppColors.borderSubtle),
      ),
    ),
  );
}
