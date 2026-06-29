/// MIUI/HyperOS deep-link targets for the permissions "Hey Lazy" needs to
/// survive Xiaomi's aggressive background killing. Component/action strings are
/// reverse-engineered and vary by MIUI version — the launcher wraps each in
/// try/catch and falls back to the app-details page.
library;

import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

class MiuiSettingTarget {
  final String key;
  final String label;
  final String? action;
  final String? package;
  final String? component;
  final Map<String, String> extras;
  const MiuiSettingTarget({
    required this.key,
    required this.label,
    this.action,
    this.package,
    this.component,
    this.extras = const {},
  });
}

/// The three MIUI permissions "Hey Lazy" needs, in setup order. `background_popup`
/// is load-bearing: without it MIUI silently blocks the background activity start
/// that surfaces the assistant over the lock screen.
List<MiuiSettingTarget> miuiTargets(String packageName) => [
      MiuiSettingTarget(
        key: 'autostart',
        label: 'Allow autostart',
        package: 'com.miui.securitycenter',
        component: 'com.miui.permcenter.autostart.AutoStartManagementActivity',
      ),
      MiuiSettingTarget(
        key: 'battery',
        label: 'No battery restrictions',
        package: 'com.miui.powerkeeper',
        component: 'com.miui.powerkeeper.ui.HiddenAppsConfigActivity',
        extras: {'package_name': packageName, 'package_label': 'LazyClaw'},
      ),
      MiuiSettingTarget(
        key: 'background_popup',
        label: 'Display pop-up while running in background',
        action: 'miui.intent.action.APP_PERM_EDITOR',
        package: 'com.miui.securitycenter',
        component: 'com.miui.permcenter.permissions.PermissionsEditorActivity',
        extras: {'extra_pkgname': packageName},
      ),
    ];

const MethodChannel _wakeChannel = MethodChannel('lazyclaw/wake');

/// True on Xiaomi/Redmi/POCO hardware, detected via the native `Build.MANUFACTURER`
/// rather than the OS-version string. HyperOS 2 (e.g. the Mi 15) dropped the
/// "miui" marker the old sniff relied on, so it hid the whole MIUI setup flow on
/// exactly the devices that need it. Async because the manufacturer comes over
/// the platform channel; cache via [xiaomiDeviceProvider].
Future<bool> resolveXiaomiDevice() async {
  if (!Platform.isAndroid) return false;
  try {
    final m =
        ((await _wakeChannel.invokeMethod<String>('deviceManufacturer')) ?? '')
            .toLowerCase();
    return m.contains('xiaomi') || m.contains('redmi') || m.contains('poco');
  } catch (_) {
    return false;
  }
}

/// Whether this is Xiaomi/Redmi/POCO hardware — gates the MIUI setup tile.
/// Resolves once via the platform channel and caches for the session.
final xiaomiDeviceProvider =
    FutureProvider<bool>((ref) => resolveXiaomiDevice());
