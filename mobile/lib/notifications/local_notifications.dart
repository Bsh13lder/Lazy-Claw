import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:timezone/data/latest_all.dart' as tz_data;
import 'package:timezone/timezone.dart' as tz;

import 'notification_actions.dart';

/// Android group keys so several notifications STACK under one expandable
/// header instead of looking like a single replaced item.
const String _kTasksGroupKey = 'lazyclaw_tasks_group';
const String _kNotificationsGroupKey = 'lazyclaw_notifications_group';
const String _kInboxGroupKey = 'lazyclaw_inbox_group';

/// Per-process monotonic counter feeding [nextNotificationId]. Top-level (not a
/// class field) so the id generator stays pure and trivially unit-testable.
int _notifSeq = 0;

/// A distinct, strictly-positive 31-bit notification id.
///
/// The old id was `DateTime.now().millisecondsSinceEpoch & 0x7FFFFFFF`, which
/// COLLIDED when two notifications fired within the same millisecond — the
/// second `_plugin.show(id, …)` then REPLACED the first, so only one was
/// visible. (This is the "2 notifications, only one shows" bug.)
///
/// Composition (fits Android's 32-bit int id, masked to a positive 31-bit
/// value):
///   - high 21 bits: low bits of the current epoch-SECOND — distinguishes app
///     sessions so a fresh launch doesn't immediately reuse (and overwrite)
///     ids left in the shade by a prior session.
///   - low 10 bits: an always-incrementing per-process counter — so up to 1024
///     notifications fired within the SAME second still get distinct ids.
///
/// Distinctness for rapid successive calls depends only on the counter, so it
/// is deterministic to unit-test without sleeping.
int nextNotificationId() {
  final clock = DateTime.now().millisecondsSinceEpoch ~/ 1000;
  final id = (((clock & 0x1FFFFF) << 10) | (_notifSeq & 0x3FF)) & 0x7FFFFFFF;
  _notifSeq++;
  // Guarantee strict positivity: a 0 clock-component AND a 0 counter is
  // astronomically unlikely, but the id must never collapse to 0.
  return id == 0 ? 1 : id;
}

/// Outcome of [LocalNotifications.scheduleTestReminder]: a [fire] time (+ whether
/// it was scheduled EXACTLY), or an [error] describing why nothing could be
/// scheduled — surfaced in the UI so a "could not schedule" is never a mystery.
class ScheduleTestResult {
  final DateTime? fire;
  final bool exact;
  final String? error;
  const ScheduleTestResult({this.fire, this.exact = false, this.error});
  bool get ok => error == null && fire != null;
}

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

    // Small icon = a WHITE silhouette of the LazyClaw claw mark on transparent
    // (`ic_stat_lazyclaw`), which Android tints per-channel. The full-colour
    // `@mipmap/ic_launcher` would render as a white blob in the status bar, so
    // it is used only as the LARGE icon on each notification instead.
    const androidSettings =
        AndroidInitializationSettings('ic_stat_lazyclaw');
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
        // Handles a "Done" action tap while the app is killed/backgrounded —
        // a separate background isolate calls this top-level entry point.
        onDidReceiveBackgroundNotificationResponse: notificationBackgroundHandler,
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

  /// Whether the OS currently allows this app to post notifications.
  ///
  /// Best-effort: queries the Android plugin's [areNotificationsEnabled]; on
  /// iOS/macOS (where there is no cheap synchronous check) it returns true,
  /// since [init] already requested permission. Returns false on null/unknown
  /// or any error — never throws.
  static Future<bool> areNotificationsEnabled() async {
    try {
      final androidImpl = _plugin.resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin>();
      if (androidImpl != null) {
        final enabled = await androidImpl.areNotificationsEnabled();
        return enabled ?? false;
      }
      // Non-Android platform — assume granted (init requested it). Best-effort.
      return true;
    } catch (_) {
      return false;
    }
  }

  /// Re-request the runtime notification permission (and, on Android, the
  /// exact-alarm permission). Returns whether notifications are granted
  /// afterwards. Best-effort — never throws.
  static Future<bool> requestPermissions() async {
    try {
      final androidImpl = _plugin.resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin>();
      if (androidImpl != null) {
        await androidImpl.requestNotificationsPermission();
        await androidImpl.requestExactAlarmsPermission();
      }
      final iosImpl = _plugin.resolvePlatformSpecificImplementation<
          IOSFlutterLocalNotificationsPlugin>();
      await iosImpl?.requestPermissions(alert: true, badge: true, sound: true);
    } catch (_) {
      // Permission API unavailable / denied — fall through to a status check.
    }
    return areNotificationsEnabled();
  }

  /// Fire an IMMEDIATE real notification on the existing notifications channel.
  /// Used by Settings → "Send test notification" to prove the basic delivery
  /// path end-to-end (bypasses scheduling). Best-effort — never throws.
  static Future<void> showTest() => showServerNotification(
        'LazyClaw test',
        'If you can see this, notifications are working ✅',
        payload: 'chat',
      );

  /// Whether the OS currently allows EXACT alarms (Android 12+). When false,
  /// scheduled reminders (`zonedSchedule` with exactAllowWhileIdle) get delayed
  /// or dropped until the user grants "Alarms & reminders". Returns true on
  /// non-Android / unknown so we don't nag where it doesn't apply. Never throws.
  static Future<bool> canScheduleExact() async {
    try {
      final androidImpl = _plugin.resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin>();
      if (androidImpl == null) return true;
      return (await androidImpl.canScheduleExactNotifications()) ?? true;
    } catch (_) {
      return true;
    }
  }

  /// SCHEDULE a real reminder [after] from now via the SAME `zonedSchedule` /
  /// exact-alarm path as task reminders. Returns the fire time + whether it was
  /// scheduled EXACTLY, or null if scheduling couldn't be set up at all.
  ///
  /// Tries an EXACT alarm first; if the OS forbids exact alarms (Android 12+
  /// without the "Alarms & reminders" grant) that call throws, so we retry an
  /// INEXACT alarm — which still fires (a little late) and needs no special
  /// grant. Diagnostic: if the immediate [showTest] fires but NEITHER scheduled
  /// mode ever arrives, the app is being frozen in the background
  /// (HyperOS/MIUI Autostart + battery). Best-effort — never throws.
  static Future<ScheduleTestResult> scheduleTestReminder({
    Duration after = const Duration(minutes: 1),
  }) async {
    if (!_initialised) {
      return const ScheduleTestResult(error: 'Notifications not initialised');
    }
    // Self-heal: the immediate test works without a timezone, but scheduling
    // needs one — (re)initialise it now if the startup attempt didn't stick.
    if (!_tzReady) await _initTimezone();
    if (!_tzReady) {
      return const ScheduleTestResult(error: 'Timezone database unavailable');
    }
    final DateTime fire;
    final tz.TZDateTime when;
    try {
      fire = DateTime.now().add(after);
      when = tz.TZDateTime.from(fire, tz.local);
    } catch (e) {
      return ScheduleTestResult(error: 'Timezone error: $e');
    }
    const androidDetails = AndroidNotificationDetails(
      'lazyclaw_task_reminders',
      'Task reminders',
      channelDescription:
          'Scheduled reminders for your tasks at their due / reminder time',
      importance: Importance.high,
      priority: Priority.high,
      category: AndroidNotificationCategory.reminder,
      showWhen: true,
      icon: 'ic_stat_lazyclaw',
      largeIcon: DrawableResourceAndroidBitmap('@mipmap/ic_launcher'),
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
    Future<void> sched(AndroidScheduleMode mode, String body) =>
        _plugin.zonedSchedule(
          nextNotificationId(),
          'LazyClaw scheduled test',
          body,
          when,
          details,
          androidScheduleMode: mode,
          uiLocalNotificationDateInterpretation:
              UILocalNotificationDateInterpretation.absoluteTime,
          payload: 'chat',
        );
    try {
      await sched(AndroidScheduleMode.exactAllowWhileIdle,
          'This fired on schedule ✅ — scheduled reminders work.');
      return ScheduleTestResult(fire: fire, exact: true);
    } catch (_) {
      // Exact alarms blocked → retry inexact (no special grant, still fires).
      try {
        await sched(AndroidScheduleMode.inexactAllowWhileIdle,
            'This fired on schedule (inexact — may be slightly late).');
        return ScheduleTestResult(fire: fire, exact: false);
      } catch (e) {
        return ScheduleTestResult(error: 'Schedule failed: $e');
      }
    }
  }

  /// Open the Android "Alarms & reminders" system screen so the user can allow
  /// exact alarms (Android 12+ — needed for on-time scheduled reminders).
  /// No-op where it doesn't apply. Never throws.
  static Future<void> openExactAlarmSettings() async {
    try {
      final androidImpl = _plugin.resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin>();
      await androidImpl?.requestExactAlarmsPermission();
    } catch (_) {
      // Settings screen unavailable on this version — ignore.
    }
  }

  /// Show a local notification with [title] and [body].
  ///
  /// Silently no-ops if the plugin was not initialised or if the user
  /// denied permission. Each call uses a fresh [nextNotificationId] so
  /// multiple concurrent notifications are preserved rather than replaced.
  static Future<void> showTaskNotification(String title, String body) async {
    if (!_initialised) return;
    try {
      final id = nextNotificationId();
      const androidDetails = AndroidNotificationDetails(
        'lazyclaw_tasks',
        'LazyClaw Tasks',
        channelDescription: 'Background task and agent approval notifications',
        importance: Importance.high,
        priority: Priority.high,
        showWhen: true,
        groupKey: _kTasksGroupKey,
        icon: 'ic_stat_lazyclaw',
        largeIcon: DrawableResourceAndroidBitmap('@mipmap/ic_launcher'),
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
      final id = nextNotificationId();
      const androidDetails = AndroidNotificationDetails(
        'lazyclaw_notifications',
        'LazyClaw Notifications',
        channelDescription:
            'Watcher alerts, background-job results, and escalations',
        importance: Importance.high,
        priority: Priority.high,
        showWhen: true,
        groupKey: _kNotificationsGroupKey,
        icon: 'ic_stat_lazyclaw',
        largeIcon: DrawableResourceAndroidBitmap('@mipmap/ic_launcher'),
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

  /// Show an inbox notification for a new channel message (WhatsApp / Email /
  /// Instagram). Uses the dedicated `lazyclaw_inbox` channel so the user can
  /// mute inbox alerts independently. The payload `'inbox'` routes the tap to
  /// `/inbox` via [onSelectNotification]. Silently no-ops when the plugin isn't
  /// initialised or permission was denied.
  static Future<void> showInboxNotification(String title, String body) async {
    if (!_initialised) return;
    try {
      final id = nextNotificationId();
      const androidDetails = AndroidNotificationDetails(
        'lazyclaw_inbox',
        'Inbox Messages',
        channelDescription:
            'New messages from WhatsApp, Email, and Instagram',
        importance: Importance.high,
        priority: Priority.high,
        showWhen: true,
        groupKey: _kInboxGroupKey,
        icon: 'ic_stat_lazyclaw',
        largeIcon: DrawableResourceAndroidBitmap('@mipmap/ic_launcher'),
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
      await _plugin.show(id, title, body, details, payload: 'inbox');
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

  /// Plugin callback for a WARM tap (app in the foreground).
  ///
  /// A "Done" ACTION tap on a task reminder completes the task in-place (same
  /// local mutation + outbox enqueue as the in-app complete) and does NOT
  /// deep-link anywhere. Any OTHER tap (a plain body tap, or a server
  /// notification) forwards the payload to the app-set [onSelectNotification]
  /// hook for deep-linking. Static so it survives AOT; reads the mutable hook at
  /// call time.
  static void _onResponse(NotificationResponse response) {
    try {
      if (isTaskDoneAction(response)) {
        // Best-effort: completeTaskFromPayload swallows its own errors. We do
        // NOT forward to onSelectNotification — completing a task shouldn't also
        // navigate the user away from where they are.
        completeTaskFromPayload(response.payload);
        return;
      }
      onSelectNotification?.call(response.payload);
    } catch (_) {
      // A handler error must never crash the notification subsystem.
    }
  }
}
