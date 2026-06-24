import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:ota_update/ota_update.dart';

import '../../providers/auth_provider.dart';
import '../../ui/ui.dart';

/// Endpoints + channel constants for the sideloadable AI-keyboard companion app.
///
/// The keyboard is a SEPARATE Android app (a LeanType fork) served over the LAN
/// by the lazyclaw gateway. These mirror `/api/mobile/{version,apk}` so the same
/// `ota_update` download+install mechanism the app uses on itself can install
/// the keyboard, just pointed at a different file.
const String kKeyboardApkPath = '/api/mobile/keyboard-apk';
const String kKeyboardVersionPath = '/api/mobile/keyboard-version';

/// Platform channel (paired with `MainActivity.kt`) that opens the system
/// input-method settings so the user can turn the keyboard on after install.
const MethodChannel _systemSettingsChannel =
    MethodChannel('com.lazyclaw/system_settings');

/// Opens the Android system keyboard / input-method settings screen.
///
/// Best-effort: a missing channel handler (e.g. iOS, or before the native side
/// is wired) resolves to `false` rather than throwing.
Future<bool> openInputMethodSettings() async {
  try {
    final ok = await _systemSettingsChannel
        .invokeMethod<bool>('openInputMethodSettings');
    return ok ?? false;
  } on PlatformException {
    return false;
  } on MissingPluginException {
    return false;
  }
}

/// Describes the published keyboard APK (version + integrity hash).
class KeyboardInfo {
  const KeyboardInfo({
    required this.version,
    required this.build,
    this.sha256,
    this.label,
  });

  final String version;
  final int build;
  final String? sha256;
  final String? label;
}

/// Fetches `/api/mobile/keyboard-version`, validating the payload at the
/// boundary. Returns `null` on an unreachable / malformed server so the caller
/// can still offer the install (the server normally provides a hash, but its
/// absence only weakens integrity verification — it is not fatal).
Future<KeyboardInfo?> fetchKeyboardInfo(WidgetRef ref) async {
  try {
    final json = await ref
        .read(apiClientProvider)
        .get<Map<String, dynamic>>(kKeyboardVersionPath);
    final version = json['version']?.toString() ?? '';
    if (version.isEmpty) return null;
    final sha = json['sha256']?.toString();
    return KeyboardInfo(
      version: version,
      build: (json['build'] as num?)?.toInt() ?? 0,
      sha256: sha == null || sha.isEmpty ? null : sha,
      label: json['label']?.toString(),
    );
  } catch (_) {
    return null;
  }
}

/// Presents the keyboard download + install dialog and drives `ota_update`
/// against the keyboard APK, rendering progress on a kit bar — the same flow
/// the app's own self-updater uses, just pointed at the keyboard endpoint.
Future<void> showKeyboardInstallDialog(
  BuildContext context,
  WidgetRef ref,
) async {
  final info = await fetchKeyboardInfo(ref);

  // Absolute APK URL from the configured base URL (strip a trailing slash).
  final baseUrl = ref.read(baseUrlProvider).replaceFirst(RegExp(r'/+$'), '');
  final apkUrl = '$baseUrl$kKeyboardApkPath';

  if (!context.mounted) return;
  await LzDialog.show<void>(
    context,
    title: 'AI Keyboard',
    barrierDismissible: false,
    content: _KeyboardInstallBody(info: info, apkUrl: apkUrl),
  );
}

/// Self-contained install flow: version line, an Install button, a progress bar
/// while downloading, and terminal success/error feedback. Owns its own
/// `OtaUpdate` stream subscription so all transient state stays local.
class _KeyboardInstallBody extends StatefulWidget {
  const _KeyboardInstallBody({required this.info, required this.apkUrl});

  final KeyboardInfo? info;
  final String apkUrl;

  @override
  State<_KeyboardInstallBody> createState() => _KeyboardInstallBodyState();
}

class _KeyboardInstallBodyState extends State<_KeyboardInstallBody> {
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
      OtaStatus.ALREADY_RUNNING_ERROR => 'An install is already in progress.',
      OtaStatus.INSTALLATION_ERROR => 'Installation failed.',
      _ => 'Install failed.',
    };
    return detail.isEmpty ? base : '$base ($detail)';
  }

  void _startInstall() {
    if (_running) return;
    setState(() {
      _running = true;
      _error = null;
      _progress = 0;
      _status = OtaStatus.DOWNLOADING;
    });
    try {
      _sub = OtaUpdate()
          .execute(widget.apkUrl, sha256checksum: widget.info?.sha256)
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
              _error = 'Install cancelled.';
              _running = false;
            } else if (event.status == OtaStatus.INSTALLATION_DONE) {
              // The system installer has been handed the APK; the OTA stream is
              // finished. Clear `_running` so the Done button is tappable even
              // if the user backs out of the installer and returns here.
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
    final info = widget.info;

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (info != null)
          Text(
            'v${info.version} (build ${info.build})',
            style: AppText.body.copyWith(
              color: AppColors.accent,
              fontWeight: FontWeight.w700,
            ),
          ),
        const SizedBox(height: AppSpacing.sm),

        // Progress / status area while running or after a terminal state.
        if (_running || installing) ...[
          LzProgressBar(value: _progress),
          const SizedBox(height: AppSpacing.sm),
          Text(
            installing
                ? (_status == OtaStatus.INSTALLATION_DONE
                    ? 'Installer launched — tap Install on the system prompt, '
                        'then come back and enable the keyboard.'
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
            'Downloads the AI Keyboard and opens the Android installer. '
            'You may need to allow "Install unknown apps" the first time.',
            style: AppText.caption.copyWith(color: AppColors.textSecondary),
          ),
        const SizedBox(height: AppSpacing.lg),

        // Action row.
        Row(
          mainAxisAlignment: MainAxisAlignment.end,
          children: [
            LzButton.ghost(
              label: installing ? 'Done' : 'Close',
              onPressed: _running ? null : () => Navigator.of(context).pop(),
            ),
            const SizedBox(width: AppSpacing.sm),
            if (!installing)
              LzButton.primary(
                label: _error != null ? 'Retry' : 'Install',
                icon: Icons.download,
                loading: _running,
                onPressed: _running ? null : _startInstall,
              ),
          ],
        ),
      ],
    );
  }
}
