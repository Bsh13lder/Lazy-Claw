import 'package:flutter/material.dart';
import '../tokens/tokens.dart';

/// A single destination in [LzBottomNav].
class LzNavDestination {
  const LzNavDestination({
    required this.label,
    required this.icon,
    required this.activeIcon,
    this.badgeCount = 0,
  });

  final String label;
  final IconData icon;
  final IconData activeIcon;

  /// Optional count rendered as a small amber dot-badge on the icon.
  final int badgeCount;
}

/// The app's custom bottom navigation bar.
///
/// Renders [destinations] across a raised [AppColors.bgSurfaceElevated] surface
/// with a hairline top border. The active destination is highlighted by an
/// animated amber "pill" indicator that slides behind the selected icon
/// ([AppMotion.base] / [AppMotion.curve]); icons + labels cross-fade between the
/// muted and accent colors.
///
/// Designed for 6 destinations (Home · Chat · Tasks · Money · Notes · Settings)
/// but works for any count.
class LzBottomNav extends StatelessWidget {
  const LzBottomNav({
    super.key,
    required this.destinations,
    required this.currentIndex,
    required this.onSelected,
  });

  final List<LzNavDestination> destinations;
  final int currentIndex;
  final ValueChanged<int> onSelected;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: const BoxDecoration(
        color: AppColors.bgSurfaceElevated,
        border: Border(
          top: BorderSide(color: AppColors.borderSubtle, width: 1),
        ),
      ),
      child: SafeArea(
        top: false,
        child: SizedBox(
          height: 62,
          child: Row(
            children: [
              for (var i = 0; i < destinations.length; i++)
                Expanded(
                  child: _NavItem(
                    destination: destinations[i],
                    selected: i == currentIndex,
                    onTap: () => onSelected(i),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _NavItem extends StatelessWidget {
  const _NavItem({
    required this.destination,
    required this.selected,
    required this.onTap,
  });

  final LzNavDestination destination;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = selected ? AppColors.accent : AppColors.textMuted;

    return Semantics(
      button: true,
      selected: selected,
      label: destination.label,
      child: InkResponse(
        onTap: onTap,
        radius: 40,
        highlightColor: Colors.transparent,
        splashColor: AppColors.accent.withValues(alpha: 0.10),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            // Icon + animated active pill behind it.
            Stack(
              alignment: Alignment.center,
              clipBehavior: Clip.none,
              children: [
                AnimatedContainer(
                  duration: AppMotion.base,
                  curve: AppMotion.curve,
                  width: selected ? 44 : 0,
                  height: 30,
                  decoration: BoxDecoration(
                    color: AppColors.accent
                        .withValues(alpha: selected ? 0.18 : 0.0),
                    borderRadius: AppRadii.rPill,
                  ),
                ),
                _Icon(destination: destination, selected: selected, color: color),
              ],
            ),
            const SizedBox(height: 4),
            AnimatedDefaultTextStyle(
              duration: AppMotion.base,
              curve: AppMotion.curve,
              style: AppText.caption.copyWith(
                color: color,
                fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                fontSize: 10.5,
              ),
              child: Text(destination.label),
            ),
          ],
        ),
      ),
    );
  }
}

class _Icon extends StatelessWidget {
  const _Icon({
    required this.destination,
    required this.selected,
    required this.color,
  });

  final LzNavDestination destination;
  final bool selected;
  final Color color;

  @override
  Widget build(BuildContext context) {
    final icon = AnimatedSwitcher(
      duration: AppMotion.fast,
      transitionBuilder: (child, anim) =>
          ScaleTransition(scale: anim, child: child),
      child: Icon(
        selected ? destination.activeIcon : destination.icon,
        key: ValueKey(selected),
        size: 24,
        color: color,
      ),
    );

    if (destination.badgeCount <= 0) return icon;

    return Stack(
      clipBehavior: Clip.none,
      children: [
        icon,
        Positioned(
          right: -4,
          top: -2,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
            constraints: const BoxConstraints(minWidth: 14),
            decoration: const BoxDecoration(
              color: AppColors.accent,
              borderRadius: AppRadii.rPill,
            ),
            child: Text(
              destination.badgeCount > 9 ? '9+' : '${destination.badgeCount}',
              textAlign: TextAlign.center,
              style: AppText.caption.copyWith(
                color: AppColors.onAccent,
                fontSize: 8.5,
                fontWeight: FontWeight.w700,
                height: 1.1,
              ),
            ),
          ),
        ),
      ],
    );
  }
}
