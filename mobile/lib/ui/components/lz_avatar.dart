import 'package:flutter/material.dart';
import '../tokens/tokens.dart';

/// A circular avatar. Renders [imageUrl] when provided, otherwise the initials
/// derived from [name] on an amber-tinted surface. Falls back to a generic
/// person glyph when neither is available.
class LzAvatar extends StatelessWidget {
  const LzAvatar({
    super.key,
    this.name,
    this.imageUrl,
    this.size = 40,
    this.gradient = false,
  });

  final String? name;
  final String? imageUrl;
  final double size;

  /// Paint the initials background with the amber-claw gradient.
  final bool gradient;

  String get _initials {
    final n = name?.trim() ?? '';
    if (n.isEmpty) return '';
    final parts = n.split(RegExp(r'\s+'));
    if (parts.length == 1) {
      return parts.first.characters.take(2).toString().toUpperCase();
    }
    return (parts.first.characters.first + parts.last.characters.first)
        .toUpperCase();
  }

  @override
  Widget build(BuildContext context) {
    if (imageUrl != null && imageUrl!.isNotEmpty) {
      return ClipOval(
        child: Image.network(
          imageUrl!,
          width: size,
          height: size,
          fit: BoxFit.cover,
          errorBuilder: (_, error, stack) => _fallback(),
        ),
      );
    }
    return _fallback();
  }

  Widget _fallback() {
    final initials = _initials;
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: gradient ? AppColors.accentGradient : null,
        color: gradient ? null : AppColors.accent.withValues(alpha: 0.16),
        border: Border.all(color: AppColors.borderSubtle),
      ),
      child: initials.isEmpty
          ? Icon(Icons.person_outline,
              size: size * 0.5, color: AppColors.accent)
          : Text(
              initials,
              style: AppText.label.copyWith(
                fontSize: size * 0.36,
                color: gradient ? AppColors.onAccent : AppColors.accent,
                fontWeight: FontWeight.w700,
              ),
            ),
    );
  }
}
