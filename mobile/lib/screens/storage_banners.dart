import 'dart:async';

import 'package:flutter/material.dart';

import '../local/app_db.dart' show resetAppDb;
import '../ui/ui.dart';

/// Builds the stack of system banners pinned at the top of an offline-first
/// screen body:
///
/// - an [LzBanner.offline] strip when the backend is unreachable, and/or
/// - an [LzBanner.degraded] strip when the local DB fell back to in-memory.
///
/// Returns `null` when neither applies, so callers can pass the result straight
/// into [LzScaffold.banner] or guard an inline `if (banners != null)`.
///
/// [onRetry] re-runs the screen's own loader (best-effort refresh from server).
/// The Reset affordance is handled here (identical across screens): it confirms,
/// wipes the local DB + its key via [resetAppDb], then tells the user to restart
/// the app so a fresh local store is rebuilt on next launch.
Widget? buildStorageBanners(
  BuildContext context, {
  required bool offline,
  required bool degraded,
  required VoidCallback onRetry,
}) {
  final banners = <Widget>[
    if (offline) const LzBanner.offline(safeAreaTop: false),
    if (degraded)
      LzBanner.degraded(
        safeAreaTop: false,
        onRetry: onRetry,
        onReset: () => unawaited(confirmResetLocalStorage(context)),
      ),
  ];
  if (banners.isEmpty) return null;
  if (banners.length == 1) return banners.first;
  return Column(mainAxisSize: MainAxisSize.min, children: banners);
}

/// Confirm + perform a local-storage reset. Shows a destructive [LzConfirm];
/// on confirm, wipes the corrupt DB file + keychain key, then surfaces a
/// SnackBar telling the user to restart the app (a live DB hot-swap needs a
/// restart — we deliberately do NOT re-override providers at runtime).
Future<void> confirmResetLocalStorage(BuildContext context) async {
  final confirmed = await LzConfirm.show(
    context,
    title: 'Reset local storage?',
    message: 'This clears the on-device cache and its encryption key, then '
        'rebuilds a fresh local database. Your synced data stays safe on the '
        "server. You'll need to restart the app to finish.",
    confirmLabel: 'Reset',
    cancelLabel: 'Cancel',
    danger: true,
  );
  if (!confirmed) return;

  await resetAppDb();
  if (!context.mounted) return;
  ScaffoldMessenger.of(context).showSnackBar(
    const SnackBar(
      content: Text('Local storage reset — restart the app to rebuild it.'),
    ),
  );
}
