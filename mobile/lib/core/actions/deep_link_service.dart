import 'dart:async';

import 'package:flutter/services.dart';
import 'package:home_widget/home_widget.dart';
import 'package:quick_actions/quick_actions.dart';

import 'app_actions.dart';

/// MethodChannel the native side ([MainActivity]) posts the system ASSIST
/// gesture over. The Dart side listens and resolves it to [AppAction.assistant].
const MethodChannel kAssistChannel = MethodChannel('lazy/assist');

/// Bridges the two native entry points — launcher long-press shortcuts
/// (`quick_actions`) and the home-screen Quick-Capture widget (`home_widget`)
/// — into a single [AppAction] callback.
///
/// The callback should stash the action in [pendingActionProvider]; the app
/// shell replays + consumes it once the router/screen is ready. This service
/// therefore stays free of any navigation or UI concerns.
class DeepLinkService {
  DeepLinkService(this._onAction);

  /// Invoked with the resolved [AppAction] whenever a shortcut or widget button
  /// fires. Never called with an unknown/unmapped trigger.
  final void Function(AppAction action) _onAction;

  final QuickActions _quickActions = const QuickActions();
  StreamSubscription<Uri?>? _widgetClickedSub;
  final MethodChannel _assistChannel = kAssistChannel;

  /// Wire up both native sources. Safe to call once after the first frame.
  ///
  ///  * Registers the [QuickActions] handler and (re)publishes the shortcut
  ///    items — re-registering every start because MIUI/HyperOS may clear them.
  ///  * Handles the cold-start cases where the app was launched BY a shortcut
  ///    (the handler fires with the launching type) or BY a widget button
  ///    ([HomeWidget.initiallyLaunchedFromHomeWidget]).
  ///  * Subscribes to warm widget clicks ([HomeWidget.widgetClicked]).
  ///
  /// Every individual step is guarded so a single platform failure (e.g. a
  /// launcher that doesn't support dynamic shortcuts) never breaks the others
  /// or the app boot.
  Future<void> init() async {
    await _initQuickActions();
    await _initHomeWidget();
    _initAssistGesture();
  }

  /// Listen for the native system ASSIST gesture (`MainActivity` forwards
  /// `Intent.ACTION_ASSIST` over [kAssistChannel] as `assist`). On every assist
  /// hop, resolve it to [AppAction.assistant] and hand it to [_onAction]. Guarded
  /// so a platform without the channel never breaks boot.
  void _initAssistGesture() {
    try {
      _assistChannel.setMethodCallHandler((call) async {
        if (call.method == 'assist') _onAction(AppAction.assistant);
        return null;
      });
    } catch (_) {
      // Channel unavailable on this platform — non-fatal.
    }
  }

  Future<void> _initQuickActions() async {
    try {
      _quickActions.initialize((type) {
        final action = appActionForShortcut(type);
        if (action != null) _onAction(action);
      });
      await _quickActions.setShortcutItems(
        kShortcutSpecs
            .map((s) => ShortcutItem(
                  type: s.type,
                  localizedTitle: s.title,
                  icon: s.icon,
                ))
            .toList(),
      );
    } catch (_) {
      // Launcher without dynamic-shortcut support — non-fatal.
    }
  }

  Future<void> _initHomeWidget() async {
    // Cold start: the app was launched by tapping a widget button.
    try {
      final launchUri = await HomeWidget.initiallyLaunchedFromHomeWidget();
      final launchAction = appActionForUri(launchUri);
      if (launchAction != null) _onAction(launchAction);
    } catch (_) {
      // No-op — no launch URI / platform unsupported.
    }

    // Warm: a widget button tapped while the app is already running.
    try {
      _widgetClickedSub = HomeWidget.widgetClicked.listen((uri) {
        final action = appActionForUri(uri);
        if (action != null) _onAction(action);
      });
    } catch (_) {
      // Stream unavailable on this platform — non-fatal.
    }
  }

  void dispose() {
    _widgetClickedSub?.cancel();
    _widgetClickedSub = null;
    _assistChannel.setMethodCallHandler(null);
  }
}
