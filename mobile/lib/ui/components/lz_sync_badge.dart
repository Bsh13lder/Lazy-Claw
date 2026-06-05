import 'package:flutter/material.dart';
import '../tokens/tokens.dart';
import 'lz_pill.dart';

/// The three sync states surfaced across the app.
enum LzSyncState {
  /// Everything is up to date with the server.
  synced,

  /// A sync is in progress / there are pending local changes.
  syncing,

  /// The backend is unreachable; changes are queued locally.
  offline,
}

/// A pill that communicates the current sync state with an icon + label.
///
/// Use [compact] to render just the spinning/cloud icon (e.g. inline on a list
/// row); the full pill is for headers and the Home dashboard.
class LzSyncBadge extends StatelessWidget {
  const LzSyncBadge({
    super.key,
    required this.state,
    this.compact = false,
  });

  final LzSyncState state;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    switch (state) {
      case LzSyncState.synced:
        return _build(
          color: AppColors.success,
          icon: Icons.cloud_done_outlined,
          label: 'Synced',
        );
      case LzSyncState.syncing:
        return _build(
          color: AppColors.info,
          icon: Icons.sync,
          label: 'Syncing…',
          spinning: true,
        );
      case LzSyncState.offline:
        return _build(
          color: AppColors.warn,
          icon: Icons.cloud_off_outlined,
          label: 'Offline',
        );
    }
  }

  Widget _build({
    required Color color,
    required IconData icon,
    required String label,
    bool spinning = false,
  }) {
    final glyph = spinning
        ? _Spinner(color: color, size: compact ? 16 : 14)
        : Icon(icon, size: compact ? 18 : 14, color: color);

    if (compact) {
      return Tooltip(message: label, child: glyph);
    }
    return LzPill(label: label, icon: icon, color: color);
  }
}

/// A continuously rotating sync glyph (for the "syncing" state).
class _Spinner extends StatefulWidget {
  const _Spinner({required this.color, required this.size});
  final Color color;
  final double size;

  @override
  State<_Spinner> createState() => _SpinnerState();
}

class _SpinnerState extends State<_Spinner>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  )..repeat();

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return RotationTransition(
      turns: _c,
      child: Icon(Icons.sync, size: widget.size, color: widget.color),
    );
  }
}
