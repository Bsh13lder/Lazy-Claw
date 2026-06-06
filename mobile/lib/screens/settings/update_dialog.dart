import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:ota_update/ota_update.dart';
import 'package:package_info_plus/package_info_plus.dart';

import '../../core/constants/app_constants.dart';
import '../../core/self_update.dart';
import '../../providers/auth_provider.dart';
import '../../ui/ui.dart';

/// Presents the "update available" dialog for [info] and drives the
/// download + install via `ota_update`, rendering progress on a kit bar.
///
/// Reads the real running version from `package_info_plus` (falling back to the
/// compile-time constants) and builds the absolute APK URL from the configured
/// gateway base URL.
Future<void> showUpdateDialog(
  BuildContext context,
  WidgetRef ref,
  UpdateInfo info,
) async {
  // Real running version for the "current → latest" line.
  String currentVersion = kAppVersion;
  int currentBuild = kAppBuild;
  try {
    final pkg = await PackageInfo.fromPlatform();
    if (pkg.version.isNotEmpty) currentVersion = pkg.version;
    currentBuild = int.tryParse(pkg.buildNumber) ?? kAppBuild;
  } catch (_) {
    // Non-platform context — keep the compile-time fallback.
  }

  // Absolute APK URL from the configured base URL (strip a trailing slash).
  final baseUrl = ref.read(baseUrlProvider).replaceFirst(RegExp(r'/+$'), '');
  final apkUrl = '$baseUrl${info.apkPath}';

  if (!context.mounted) return;
  await LzDialog.show<void>(
    context,
    title: 'Update available',
    barrierDismissible: false,
    content: _UpdateDialogBody(
      currentVersion: currentVersion,
      currentBuild: currentBuild,
      info: info,
      apkUrl: apkUrl,
    ),
  );
}

/// Self-contained update flow: version delta, an Update button, a progress bar
/// while downloading, and terminal success/error feedback. Owns its own
/// `OtaUpdate` stream subscription so all transient state stays local.
class _UpdateDialogBody extends StatefulWidget {
  const _UpdateDialogBody({
    required this.currentVersion,
    required this.currentBuild,
    required this.info,
    required this.apkUrl,
  });

  final String currentVersion;
  final int currentBuild;
  final UpdateInfo info;
  final String apkUrl;

  @override
  State<_UpdateDialogBody> createState() => _UpdateDialogBodyState();
}

class _UpdateDialogBodyState extends State<_UpdateDialogBody> {
  StreamSubscription<OtaEvent>? _sub;
  OtaStatus? _status;
  double _progress = 0;
  bool _running = false;
  String? _error;

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }

  bool _isErrorStatus(OtaStatus s) {
    switch (s) {
      case OtaStatus.ALREADY_RUNNING_ERROR:
      case OtaStatus.INSTALLATION_ERROR:
      case OtaStatus.PERMISSION_NOT_GRANTED_ERROR:
      case OtaStatus.INTERNAL_ERROR:
      case OtaStatus.DOWNLOAD_ERROR:
      case OtaStatus.CHECKSUM_ERROR:
        return true;
      case OtaStatus.DOWNLOADING:
      case OtaStatus.INSTALLING:
      case OtaStatus.INSTALLATION_DONE:
      case OtaStatus.CANCELED:
        return false;
    }
  }

  String _errorMessage(OtaEvent e) {
    final detail = (e.value ?? '').trim();
    final base = switch (e.status) {
      OtaStatus.PERMISSION_NOT_GRANTED_ERROR =>
        'Install permission denied. Allow "Install unknown apps" for LazyClaw.',
      OtaStatus.CHECKSUM_ERROR =>
        'Checksum mismatch — the download was corrupted. Try again.',
      OtaStatus.DOWNLOAD_ERROR => 'Download failed — check your connection.',
      OtaStatus.ALREADY_RUNNING_ERROR => 'An update is already in progress.',
      OtaStatus.INSTALLATION_ERROR => 'Installation failed.',
      _ => 'Update failed.',
    };
    return detail.isEmpty ? base : '$base ($detail)';
  }

  void _startUpdate() {
    if (_running) return;
    setState(() {
      _running = true;
      _error = null;
      _progress = 0;
      _status = OtaStatus.DOWNLOADING;
    });
    try {
      _sub = OtaUpdate()
          .execute(widget.apkUrl, sha256checksum: widget.info.sha256)
          .listen(
        (event) {
          if (!mounted) return;
          setState(() {
            _status = event.status;
            if (event.status == OtaStatus.DOWNLOADING) {
              final pct = int.tryParse(event.value ?? '');
              if (pct != null) _progress = pct / 100.0;
            } else if (event.status == OtaStatus.INSTALLING ||
                event.status == OtaStatus.INSTALLATION_DONE) {
              _progress = 1.0;
            }
            if (_isErrorStatus(event.status)) {
              _error = _errorMessage(event);
              _running = false;
            } else if (event.status == OtaStatus.CANCELED) {
              _error = 'Update cancelled.';
              _running = false;
            } else if (event.status == OtaStatus.INSTALLATION_DONE) {
              // The system installer has been handed the APK; the OTA stream
              // is finished. Clear `_running` so the Done button is tappable
              // even if the user backs out of the installer and returns here.
              _running = false;
            }
          });
        },
        onError: (Object e) {
          if (!mounted) return;
          setState(() {
            _error = e.toString();
            _running = false;
          });
        },
      );
    } catch (e) {
      setState(() {
        _error = e.toString();
        _running = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final installing = _status == OtaStatus.INSTALLING ||
        _status == OtaStatus.INSTALLATION_DONE;

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text(
              'v${widget.currentVersion} (${widget.currentBuild})',
              style: AppText.body.copyWith(color: AppColors.textSecondary),
            ),
            const SizedBox(width: AppSpacing.sm),
            const Icon(Icons.arrow_forward,
                size: 16, color: AppColors.textMuted),
            const SizedBox(width: AppSpacing.sm),
            Text(
              'v${widget.info.version} (${widget.info.build})',
              style: AppText.body.copyWith(
                color: AppColors.accent,
                fontWeight: FontWeight.w700,
              ),
            ),
          ],
        ),
        const SizedBox(height: AppSpacing.md),

        // Progress / status area while running or after a terminal state.
        if (_running || installing) ...[
          LzProgressBar(value: _progress),
          const SizedBox(height: AppSpacing.sm),
          Text(
            installing
                ? (_status == OtaStatus.INSTALLATION_DONE
                    ? 'Installer launched — tap Done.'
                    : 'Launching installer…')
                : 'Downloading… ${(_progress * 100).round()}%',
            style: AppText.caption.copyWith(color: AppColors.textSecondary),
          ),
        ] else if (_error != null) ...[
          Text(
            _error!,
            style: AppText.caption.copyWith(color: AppColors.error),
          ),
        ] else
          Text(
            'A newer version is available. Download and install it now?',
            style: AppText.caption.copyWith(color: AppColors.textSecondary),
          ),
        const SizedBox(height: AppSpacing.lg),

        // Action row.
        Row(
          mainAxisAlignment: MainAxisAlignment.end,
          children: [
            LzButton.ghost(
              label: installing ? 'Done' : 'Close',
              onPressed:
                  _running ? null : () => Navigator.of(context).pop(),
            ),
            const SizedBox(width: AppSpacing.sm),
            if (!installing)
              LzButton.primary(
                label: _error != null ? 'Retry' : 'Update',
                icon: Icons.system_update_alt,
                loading: _running,
                onPressed: _running ? null : _startUpdate,
              ),
          ],
        ),
      ],
    );
  }
}
