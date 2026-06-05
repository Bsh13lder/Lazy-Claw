import 'package:flutter_local_notifications/flutter_local_notifications.dart';

/// Thin wrapper around flutter_local_notifications.
///
/// Call [init] once at app startup (before any [showTaskNotification] call).
/// All methods are safe to call if permission was denied or init was skipped
/// — they fail silently and never throw.
class LocalNotifications {
  static final FlutterLocalNotificationsPlugin _plugin =
      FlutterLocalNotificationsPlugin();

  static bool _initialised = false;

  /// Initialise the plugin and request Android 13+ runtime permission.
  ///
  /// Safe to call multiple times — subsequent calls are no-ops.
  static Future<void> init() async {
    if (_initialised) return;

    const androidSettings =
        AndroidInitializationSettings('@mipmap/ic_launcher');
    const darwinSettings = DarwinInitializationSettings(
      requestAlertPermission: true,
      requestBadgePermission: true,
      requestSoundPermission: true,
    );
    const initSettings = InitializationSettings(
      android: androidSettings,
      iOS: darwinSettings,
      macOS: darwinSettings,
    );

    try {
      await _plugin.initialize(initSettings);
      _initialised = true;

      // Request Android 13+ runtime POST_NOTIFICATIONS permission.
      // Graceful degradation: if the platform doesn't support it or the
      // user denies, notifications simply won't appear — no crash.
      final androidImpl = _plugin
          .resolvePlatformSpecificImplementation<
              AndroidFlutterLocalNotificationsPlugin>();
      await androidImpl?.requestNotificationsPermission();
    } catch (_) {
      // Never crash the app if notifications can't be set up.
    }
  }

  /// Show a local notification with [title] and [body].
  ///
  /// Silently no-ops if the plugin was not initialised or if the user
  /// denied permission. Each call increments the notification ID so
  /// multiple concurrent notifications are preserved rather than replaced.
  static Future<void> showTaskNotification(String title, String body) async {
    if (!_initialised) return;
    try {
      final id = DateTime.now().millisecondsSinceEpoch & 0x7FFFFFFF;
      const androidDetails = AndroidNotificationDetails(
        'lazyclaw_tasks',
        'LazyClaw Tasks',
        channelDescription: 'Background task and agent approval notifications',
        importance: Importance.high,
        priority: Priority.high,
        showWhen: true,
      );
      const darwinDetails = DarwinNotificationDetails(
        presentAlert: true,
        presentBadge: true,
        presentSound: true,
      );
      const details = NotificationDetails(
        android: androidDetails,
        iOS: darwinDetails,
        macOS: darwinDetails,
      );
      await _plugin.show(id, title, body, details);
    } catch (_) {
      // Silently ignore — permission denied, plugin not ready, etc.
    }
  }
}
