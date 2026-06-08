import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

/// A small, fixed palette of tasteful project accents. Stored as `"#RRGGBB"`
/// hex strings — the same shape the backend round-trips. Kept deliberately
/// short so the picker stays a single clean row of swatches (no heavy
/// color-picker dependency). Tones are tuned to read well on the dark surfaces.
const List<String> kProjectColorPalette = <String>[
  '#FF8A3D', // amber claw (accent)
  '#FF6B6B', // coral
  '#FFB020', // gold
  '#3DD68C', // mint
  '#2DD4BF', // teal
  '#5AA9FF', // sky
  '#8B7CFF', // indigo
  '#C46BFF', // violet
  '#FF7FB6', // pink
  '#9AA0AE', // slate
];

/// Parse a `"#RRGGBB"` (or `"#AARRGGBB"`) hex string into a [Color]. Returns
/// null for null/blank/malformed input so callers can fall back gracefully.
Color? projectColorOf(String? hex) {
  if (hex == null) return null;
  var s = hex.trim();
  if (s.isEmpty) return null;
  if (s.startsWith('#')) s = s.substring(1);
  if (s.length == 6) s = 'FF$s'; // assume opaque
  if (s.length != 8) return null;
  final value = int.tryParse(s, radix: 16);
  if (value == null) return null;
  return Color(value);
}

/// A colored dot for a project. Falls back to a hollow outlined dot (the
/// "no color" look) when [hex] is null/invalid.
class ProjectColorDot extends StatelessWidget {
  const ProjectColorDot({super.key, required this.hex, this.size = 10});

  final String? hex;
  final double size;

  @override
  Widget build(BuildContext context) {
    final color = projectColorOf(hex);
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: color ?? Colors.transparent,
        shape: BoxShape.circle,
        border: color == null
            ? Border.all(color: AppColors.borderDefault, width: 1.5)
            : null,
      ),
    );
  }
}

/// A row of selectable color swatches drawn from [kProjectColorPalette]. The
/// currently-[selected] hex (case-insensitive) shows a check + ring. Tapping a
/// swatch calls [onSelected] with its hex string.
class ProjectColorSwatches extends StatelessWidget {
  const ProjectColorSwatches({
    super.key,
    required this.selected,
    required this.onSelected,
  });

  final String? selected;
  final ValueChanged<String> onSelected;

  @override
  Widget build(BuildContext context) {
    final sel = selected?.toUpperCase();
    return Wrap(
      spacing: AppSpacing.md,
      runSpacing: AppSpacing.md,
      children: [
        for (final hex in kProjectColorPalette)
          _Swatch(
            hex: hex,
            isSelected: sel == hex.toUpperCase(),
            onTap: () => onSelected(hex),
          ),
      ],
    );
  }
}

class _Swatch extends StatelessWidget {
  const _Swatch({
    required this.hex,
    required this.isSelected,
    required this.onTap,
  });

  final String hex;
  final bool isSelected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = projectColorOf(hex) ?? AppColors.textMuted;
    return Semantics(
      button: true,
      selected: isSelected,
      label: 'Color $hex',
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          width: 36,
          height: 36,
          decoration: BoxDecoration(
            color: color,
            shape: BoxShape.circle,
            border: Border.all(
              color: isSelected ? AppColors.textPrimary : Colors.transparent,
              width: 2,
            ),
          ),
          child: isSelected
              ? const Icon(Icons.check_rounded,
                  size: 18, color: AppColors.onAccent)
              : null,
        ),
      ),
    );
  }
}
