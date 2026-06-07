import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:timezone/data/latest_all.dart' as tz_data;
import 'package:timezone/timezone.dart' as tz;

/// Thin wrapper around flutter_local_notifications.
///
/// Call [init] once at app startup (before any [showTaskNotification] or any
/// scheduled-reminder call). [init] also primes the timezone database and the
/// device-local location so [TaskReminderService.scheduleForTask] can build
/// `TZDateTime`s for `zonedSchedule`.
///
/// All methods are safe to call if permission was denied or init was skipped
/// — they fail silently and never throw.
class LocalNotifications {
  static final FlutterLocalNotificationsPlugin _plugin =
      FlutterLocalNotificationsPlugin();

  static bool _initialised = false;
  static bool _tzReady = false;

  /// Invoked (on the main isolate) when the user TAPS a notification while the
  /// app is running. Receives the notification's `payload`. Wired by the app
  /// shell to deep-link into the relevant tab. Mutable + read at call time so
  /// it can be set AFTER [init] (which binds the plugin callback once).
  static void Function(String? payload)? onSelectNotification;

  /// The shared plugin instance. Other helpers (e.g. [TaskReminderService])
  /// REUSE this single instance rather than constructing a second plugin.
  static FlutterLocalNotificationsPlugin get plugin => _plugin;

  /// Whether the timezone database + local location have been initialised.
  /// Scheduling reminders before this is true would throw, so callers gate on
  /// it (the service does so internally).
  static bool get timezoneReady => _tzReady;

  /// Initialise the plugin + timezone db and request Android 13+ runtime
  /// notification permission and (best-effort) the exact-alarm permission.
  ///
  /// Safe to call multiple times — subsequent calls are no-ops.
  static Future<void> init() async {
    if (_initialised) return;

    // Prime the timezone database first so zonedSchedule has a local location.
    await _initTimezone();

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
      await _plugin.initialize(
        initSettings,
        onDidReceiveNotificationResponse: _onResponse,
      );
      _initialised = true;

      // Request Android 13+ runtime POST_NOTIFICATIONS permission and the
      // exact-alarm permission (Android 12+). Graceful degradation: if the
      // platform doesn't support them or the user denies, notifications simply
      // won't appear / will be scheduled inexactly — no crash.
      final androidImpl = _plugin
          .resolvePlatformSpecificImplementation<
              AndroidFlutterLocalNotificationsPlugin>();
      await androidImpl?.requestNotificationsPermission();
      await androidImpl?.requestExactAlarmsPermission();
    } catch (_) {
      // Never crash the app if notifications can't be set up.
    }
  }

  /// Prime the IANA timezone database and set the device-local location so
  /// `tz.TZDateTime` resolves correctly. Falls back to UTC if the device zone
  /// can't be resolved. Idempotent.
  static Future<void> _initTimezone() async {
    if (_tzReady) return;
    try {
      tz_data.initializeTimeZones();
      final name = await FlutterTimezone.getLocalTimezone();
      tz.setLocalLocation(tz.getLocation(name));
      _tzReady = true;
    } catch (_) {
      // Could not resolve the device zone — fall back to UTC so scheduling
      // still works (times will be interpreted as UTC rather than crashing).
      try {
        tz.setLocalLocation(tz.getLocation('UTC'));
        _tzReady = true;
      } catch (_) {
        // timezone db unavailable entirely — leave _tzReady false; callers
        // that depend on it will skip scheduling rather than throw.
      }
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

  /// Show a SERVER-originated notification (watcher fired, background job done,
  /// escalation, …) on a dedicated "Notifications" channel so the user can mute
  /// it independently of task reminders.
  ///
  /// [payload] is delivered to [onSelectNotification] when the user taps the
  /// notification, enabling a best-effort deep-link (e.g. `'chat'`). Silently
  /// no-ops when the plugin isn't initialised or permission was denied.
  static Future<void> showServerNotification(
    String title,
    String body, {
    String? payload,
  }) async {
    if (!_initialised) return;
    try {
      final id = DateTime.now().millisecondsSinceEpoch & 0x7FFFFFFF;
      const androidDetails = AndroidNotificationDetails(
        'lazyclaw_notifications',
        'LazyClaw Notifications',
        channelDescription:
            'Watcher alerts, background-job results, and escalations',
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
      await _plugin.show(id, title, body, details, payload: payload);
    } catch (_) {
      // Silently ignore — permission denied, plugin not ready, etc.
    }
  }

  /// If the app was COLD-STARTED by tapping a notification, returns that
  /// notification's payload (else null). Lets the shell deep-link on launch.
  /// Never throws.
  static Future<String?> consumeLaunchPayload() async {
    try {
      final details = await _plugin.getNotificationAppLaunchDetails();
      if (details?.didNotificationLaunchApp == true) {
        return details?.notificationResponse?.payload;
      }
    } catch (_) {
      // Platform without launch details — ignore.
    }
    return null;
  }

  /// Plugin callback for a WARM tap. Forwards the payload to the app-set hook.
  /// Top-level/static so it survives AOT; reads the mutable hook at call time.
  static void _onResponse(NotificationResponse response) {
    try {
      onSelectNotification?.call(response.payload);
    } catch (_) {
      // A handler error must never crash the notification subsystem.
    }
  }
}
