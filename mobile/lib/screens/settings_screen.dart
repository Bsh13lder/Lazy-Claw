import 'dart:io' show Platform;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:workmanager/workmanager.dart';

import '../core/config/server_config.dart';
import '../core/constants/app_constants.dart';
import '../core/crash_log.dart';
import '../core/due_date.dart';
import '../core/reminder_lead.dart';
import '../core/self_update.dart';
import '../notifications/local_notifications.dart';
import 'settings/update_dialog.dart';
import '../providers/auth_provider.dart';
import '../providers/budgets_provider.dart';
import '../providers/notes_provider.dart';
import '../providers/notifications_channel_provider.dart';
import '../providers/tasks_provider.dart';
import '../repositories/notifications_repository.dart';
import '../sync/background_sync.dart';
import '../sync/budgets_sync.dart';
import '../sync/note_sync.dart';
import '../sync/task_sync.dart';
import '../providers/settings_repo_provider.dart';
import '../repositories/settings_repository.dart';
import '../ui/ui.dart';
import 'settings/conflicts_sheet.dart';
import 'settings/settings_prefs.dart';

// ── Connection-test provider ─────────────────────────────────────────────────

/// Auto-dispose async provider that fires a one-shot ping via [Reachability].
/// Invalidated by the "Test connection" button or after a URL save.
final _connectionTestProvider = FutureProvider.autoDispose<bool>((ref) {
  final reach = ref.watch(reachabilityProvider);
  return reach.refresh();
});

// ── Sync-all notifier ────────────────────────────────────────────────────────

class _SyncState {
  final bool running;
  final String? summary;
  final String? error;

  const _SyncState({this.running = false, this.summary, this.error});

  _SyncState copyWith({bool? running, String? summary, String? error}) =>
      _SyncState(
        running: running ?? this.running,
        summary: summary ?? this.summary,
        error: error ?? this.error,
      );
}

class _SyncAllNotifier extends StateNotifier<_SyncState> {
  _SyncAllNotifier() : super(const _SyncState());

  Future<void> syncAll({
    required TaskSync tasks,
    required NoteSync notes,
    required BudgetsSync budgets,
  }) async {
    if (state.running) return;
    state = state.copyWith(running: true, summary: null, error: null);
    try {
      // Run all three engines concurrently; collect typed results individually.
      late final SyncResult taskRes;
      late final NoteSyncResult noteRes;
      late final BudgetsSyncResult budgetRes;
      await Future.wait([
        tasks.sync().then((r) => taskRes = r),
        notes.sync().then((r) => noteRes = r),
        budgets.sync().then((r) => budgetRes = r),
      ]);

      final pushed = taskRes.pushed + noteRes.pushed + budgetRes.pushed;
      final pulled = taskRes.pulled + noteRes.pulled + budgetRes.pulled;
      final conflicts =
          taskRes.conflicts + noteRes.conflicts + budgetRes.conflicts;

      final parts = <String>[];
      if (pushed > 0) parts.add('$pushed pushed');
      if (pulled > 0) parts.add('$pulled pulled');
      if (conflicts > 0) parts.add('$conflicts conflicts');
      if (parts.isEmpty) parts.add('Up to date');

      state = state.copyWith(running: false, summary: parts.join(' · '));
    } catch (e) {
      state = state.copyWith(running: false, error: e.toString());
    }
  }
}

final _syncAllProvider =
    StateNotifierProvider.autoDispose<_SyncAllNotifier, _SyncState>(
  (_) => _SyncAllNotifier(),
);

// ── WorkManager reschedule helper (best-effort) ──────────────────────────────

Future<void> _rescheduleBackgroundSync(SyncInterval interval) async {
  try {
    if (interval == SyncInterval.off) {
      await Workmanager().cancelByUniqueName(kTaskSyncUniqueName);
    } else {
      await Workmanager().initialize(backgroundSyncDispatcher);
      await Workmanager().registerPeriodicTask(
        kTaskSyncUniqueName,
        kTaskSyncTaskName,
        frequency: Duration(minutes: interval.minutes),
        existingWorkPolicy: ExistingWorkPolicy.replace,
        constraints: Constraints(networkType: NetworkType.connected),
      );
    }
  } catch (_) {
    // WorkManager may be unavailable on some configs — ignore silently.
  }
}

// ── Settings Screen ──────────────────────────────────────────────────────────

class SettingsScreen extends ConsumerStatefulWidget {
  const SettingsScreen({super.key});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> {
  final _urlController = TextEditingController();
  bool _savingUrl = false;
  String? _urlError;
  bool _testingConnection = false;
  bool _checkingUpdate = false;

  @override
  void initState() {
    super.initState();
    _urlController.text = ref.read(baseUrlProvider);
    // Silently check for a newer build when Settings opens and populate the
    // shared provider so the "Update available" tile can surface itself. We do
    // NOT show the dialog automatically — that would be intrusive on every open.
    // This complements the one-shot startup check in main.dart.
    Future.microtask(_silentUpdateCheck);
  }

  /// Background update probe. [SelfUpdateService.checkForUpdate] never throws and
  /// collapses "up to date" / "unreachable" into null, so a null result simply
  /// leaves the provider untouched (no tile, no error noise).
  Future<void> _silentUpdateCheck() async {
    final info = await ref.read(selfUpdateServiceProvider).checkForUpdate();
    if (!mounted || info == null) return;
    ref.read(updateAvailableProvider.notifier).state = info;
  }

  @override
  void dispose() {
    _urlController.dispose();
    super.dispose();
  }

  // ── URL actions ───────────────────────────────────────────────────────────

  Future<void> _saveUrl() async {
    final raw = _urlController.text.trim();
    if (raw.isEmpty) {
      setState(() => _urlError = 'URL cannot be empty');
      return;
    }
    setState(() {
      _savingUrl = true;
      _urlError = null;
    });
    try {
      final normalized = ServerConfig.normalizeBaseUrl(raw);
      await ServerConfig.save(normalized);
      ref.read(baseUrlProvider.notifier).state = normalized;
      _urlController.text = normalized;
      ref.invalidate(_connectionTestProvider);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Gateway URL saved')),
        );
      }
    } catch (e) {
      setState(() => _urlError = e.toString());
    } finally {
      if (mounted) setState(() => _savingUrl = false);
    }
  }

  Future<void> _testConnection() async {
    if (_testingConnection) return;
    setState(() => _testingConnection = true);
    // Persist the current input before probing.
    final currentText = _urlController.text.trim();
    if (currentText != ref.read(baseUrlProvider)) {
      await _saveUrl();
    }
    ref.invalidate(_connectionTestProvider);
    await ref.read(_connectionTestProvider.future).catchError((_) => false);
    if (mounted) setState(() => _testingConnection = false);
  }

  // ── Logout ────────────────────────────────────────────────────────────────

  Future<void> _logout() async {
    final prefs = ref.read(settingsPrefsProvider).valueOrNull;
    final wipe = prefs?.wipeOnLogout ?? false;

    final confirmed = await LzConfirm.show(
      context,
      title: 'Log out?',
      message: wipe
          ? 'Your local cache will be cleared on logout. '
              'You will need to log in again.'
          : 'You will need to log in again to use the app.',
      confirmLabel: 'Log out',
      cancelLabel: 'Cancel',
      danger: true,
    );
    if (!confirmed || !mounted) return;

    if (wipe) {
      try {
        final db = ref.read(appDatabaseProvider);
        for (final table in [
          'task_cache',
          'note_cache',
          'project_cache',
          'expense_cache',
          'outbox',
          'conflicts',
          'sync_state',
        ]) {
          await db.delete(table);
        }
      } catch (_) {
        // Non-fatal — proceed with logout regardless.
      }
    }
    await ref.read(authProvider.notifier).logout();
  }

  // ── Clear cache ───────────────────────────────────────────────────────────

  Future<void> _clearCache() async {
    final confirmed = await LzConfirm.show(
      context,
      title: 'Clear local cache?',
      message:
          'All locally cached tasks, notes, and budgets will be removed. '
          'Un-synced outbox changes will also be lost. '
          'Server data is not affected.',
      confirmLabel: 'Clear',
      cancelLabel: 'Cancel',
      danger: true,
    );
    if (!confirmed || !mounted) return;

    try {
      final db = ref.read(appDatabaseProvider);
      await Future.wait([
        db.delete('task_cache'),
        db.delete('note_cache'),
        db.delete('project_cache'),
        db.delete('expense_cache'),
        db.delete('outbox'),
        db.delete('conflicts'),
        db.delete('sync_state'),
      ]);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Local cache cleared')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to clear cache: $e')),
        );
      }
    }
  }

  // ── Conflicts sheet ───────────────────────────────────────────────────────

  Future<void> _viewConflicts() => LzBottomSheet.show<void>(
        context,
        title: 'Sync conflicts',
        builder: (_) => const ConflictsSheetContent(),
      );

  // ── Sync now ──────────────────────────────────────────────────────────────

  Future<void> _syncNow() => ref.read(_syncAllProvider.notifier).syncAll(
        tasks: ref.read(taskSyncProvider),
        notes: ref.read(noteSyncProvider),
        budgets: ref.read(budgetsSyncProvider),
      );

  // ── Check for update ──────────────────────────────────────────────────────

  Future<void> _checkForUpdate() async {
    if (_checkingUpdate) return;
    setState(() => _checkingUpdate = true);
    try {
      final info = await ref.read(selfUpdateServiceProvider).checkForUpdate();
      if (!mounted) return;
      if (info != null) {
        // Seed the shared provider so other surfaces can react, then offer the
        // download/install flow.
        ref.read(updateAvailableProvider.notifier).state = info;
        await showUpdateDialog(context, ref, info);
        return;
      }
      // `checkForUpdate` collapses "up to date" and "unreachable" into null.
      // Probe reachability once to pick the right message.
      final reachable =
          await ref.read(reachabilityProvider).refresh().catchError((_) => false);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            reachable
                ? 'You are on the latest version.'
                : 'Could not check for updates — server unreachable.',
          ),
        ),
      );
    } finally {
      if (mounted) setState(() => _checkingUpdate = false);
    }
  }

  // ── Android intent helpers ────────────────────────────────────────────────

  void _openBatterySettings() {
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text(
          'Go to Settings → Apps → LazyClaw → Battery → No restrictions.',
        ),
        duration: Duration(seconds: 5),
      ),
    );
  }

  void _openAutostartSettings() {
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text(
          'Go to Settings → Apps → LazyClaw → Autostart → Enable.',
        ),
        duration: Duration(seconds: 5),
      ),
    );
  }

  // ── Home-screen widget / shortcut help (MIUI / HyperOS) ────────────────────

  /// Doc-only dialog: how to surface the Quick-Capture home-screen widget and
  /// the app-icon quick actions on Xiaomi/HyperOS, where aggressive battery
  /// management silently kills widgets and clears dynamic shortcuts unless
  /// Autostart is enabled and the battery policy is "No restrictions".
  Future<void> _showWidgetHelp() => LzDialog.show<void>(
        context,
        title: 'Home-screen widgets (Xiaomi / HyperOS)',
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: const [
            _HelpStep(
              n: '1',
              text: 'Settings → Apps → LazyClaw → Autostart → Enable. '
                  'HyperOS/MIUI clears widgets & long-press shortcuts otherwise.',
            ),
            SizedBox(height: AppSpacing.md),
            _HelpStep(
              n: '2',
              text: 'Settings → Apps → LazyClaw → Battery saver → '
                  'No restrictions.',
            ),
            SizedBox(height: AppSpacing.md),
            _HelpStep(
              n: '3',
              text: 'Add the widget: long-press an empty spot on your home '
                  'screen → Widgets → LazyClaw → drag "Quick Capture" '
                  '(+ Task / + Expense / + Note / Chat).',
            ),
            SizedBox(height: AppSpacing.md),
            _HelpStep(
              n: '4',
              text: 'App-icon shortcuts: long-press the LazyClaw icon for '
                  'Add task · Add expense · Chat · New note.',
            ),
          ],
        ),
        actions: [
          LzButton.primary(
            label: 'Got it',
            onPressed: () => Navigator.of(context).pop(),
          ),
        ],
      );

  // ── Build ─────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final authState = ref.watch(authProvider);
    final username = authState.user?.username ?? '—';
    final displayName = authState.user?.displayName;
    final isReachable = ref.watch(reachableProvider);
    final syncState = ref.watch(_syncAllProvider);
    final prefsAsync = ref.watch(settingsPrefsProvider);

    return Scaffold(
      backgroundColor: AppColors.bgBase,
      appBar: AppBar(
        backgroundColor: AppColors.bgSurfaceElevated,
        foregroundColor: AppColors.textPrimary,
        title: Text('Settings', style: AppText.titleL),
        elevation: 0,
      ),
      body: ListView(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.lg,
        ),
        children: [
          // 0. Auto-surfaced "Update available" banner — self-hides when there
          // is no pending update, so it can sit unconditionally at the top.
          const SettingsUpdateTile(),

          // 1. Account
          _buildAccountSection(username: username, displayName: displayName),
          AppSpacing.vGap(AppSpacing.xl),

          // 2. Server
          _buildServerSection(isReachable: isReachable),
          AppSpacing.vGap(AppSpacing.xl),

          // 3. Sync
          prefsAsync.when(
            loading: () => const LzSection(
              title: 'Sync',
              child: LzCard(
                child: SizedBox(
                  height: 80,
                  child: Center(child: CircularProgressIndicator()),
                ),
              ),
            ),
            error: (_, _) => const SizedBox.shrink(),
            data: (prefs) =>
                _buildSyncSection(prefs: prefs, syncState: syncState),
          ),
          AppSpacing.vGap(AppSpacing.xl),

          // 4. Notifications
          prefsAsync.when(
            loading: () => const SizedBox.shrink(),
            error: (_, _) => const SizedBox.shrink(),
            data: (prefs) => _buildNotificationsSection(prefs: prefs),
          ),
          AppSpacing.vGap(AppSpacing.xl),

          // 5. Android-only battery/autostart help
          if (Platform.isAndroid) ...[
            _buildAndroidSection(),
            AppSpacing.vGap(AppSpacing.xl),
          ],

          // 6. Power tools link
          _buildPowerToolsSection(),
          AppSpacing.vGap(AppSpacing.xl),

          // 7. Brain / ECO mode
          _buildEcoSection(),
          AppSpacing.vGap(AppSpacing.xl),

          // 7b. Operating mode (ADR-0005)
          _buildOperatingModeSection(),
          AppSpacing.vGap(AppSpacing.xl),

          // 8. Permissions
          _buildPermissionsSection(),
          AppSpacing.vGap(AppSpacing.xl),

          // 9. About
          _buildAboutSection(),
          AppSpacing.vGap(AppSpacing.xxl),
        ],
      ),
    );
  }

  // ── Section builders ──────────────────────────────────────────────────────

  Widget _buildAccountSection({
    required String username,
    String? displayName,
  }) {
    final name = displayName ?? username;
    return LzSection(
      title: 'Account',
      child: LzCard(
        padding: EdgeInsets.zero,
        child: Column(
          children: [
            LzListTile(
              leading: LzAvatar(name: name, gradient: true),
              title: name,
              subtitle: displayName != null ? '@$username' : null,
            ),
            const Divider(height: 1, color: AppColors.borderSubtle),
            LzListTile(
              leading:
                  const Icon(Icons.logout, color: AppColors.error, size: 20),
              title: 'Log out',
              titleStyle: AppText.body.copyWith(color: AppColors.error),
              onTap: _logout,
              trailing: const Icon(
                Icons.chevron_right,
                size: 18,
                color: AppColors.textMuted,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildServerSection({required bool isReachable}) {
    final connTest = ref.watch(_connectionTestProvider);
    final dot = isReachable
        ? const LzStatusDot.success(glow: true)
        : const LzStatusDot.error();

    return LzSection(
      title: 'Server',
      child: LzCard(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Reachability status dot
            Row(
              children: [
                dot,
                const SizedBox(width: AppSpacing.sm),
                Text(
                  isReachable ? 'Connected' : 'Unreachable',
                  style: AppText.caption.copyWith(
                    color:
                        isReachable ? AppColors.success : AppColors.error,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.md),

            // URL editor
            TextField(
              controller: _urlController,
              style: AppText.body,
              decoration: InputDecoration(
                labelText: 'Gateway URL',
                labelStyle: AppText.caption,
                hintText: kDefaultBaseUrl,
                hintStyle: AppText.caption,
                errorText: _urlError,
                enabledBorder: OutlineInputBorder(
                  borderRadius: AppRadii.rMd,
                  borderSide:
                      const BorderSide(color: AppColors.borderDefault),
                ),
                focusedBorder: OutlineInputBorder(
                  borderRadius: AppRadii.rMd,
                  borderSide: const BorderSide(color: AppColors.accent),
                ),
                errorBorder: OutlineInputBorder(
                  borderRadius: AppRadii.rMd,
                  borderSide: const BorderSide(color: AppColors.error),
                ),
                focusedErrorBorder: OutlineInputBorder(
                  borderRadius: AppRadii.rMd,
                  borderSide: const BorderSide(color: AppColors.error),
                ),
                filled: true,
                fillColor: AppColors.bgSurfaceHover,
                contentPadding: const EdgeInsets.symmetric(
                  horizontal: AppSpacing.md,
                  vertical: AppSpacing.md,
                ),
                suffixIcon: _savingUrl
                    ? const Padding(
                        padding: EdgeInsets.all(12),
                        child: SizedBox(
                          width: 20,
                          height: 20,
                          child:
                              CircularProgressIndicator(strokeWidth: 2),
                        ),
                      )
                    : IconButton(
                        icon: const Icon(Icons.save_outlined,
                            color: AppColors.accent),
                        tooltip: 'Save URL',
                        onPressed: _saveUrl,
                      ),
              ),
              keyboardType: TextInputType.url,
              textInputAction: TextInputAction.done,
              onSubmitted: (_) => _saveUrl(),
            ),
            const SizedBox(height: AppSpacing.md),

            // Test connection
            LzButton.secondary(
              label: _testingConnection ? 'Testing…' : 'Test connection',
              icon: Icons.wifi_tethering,
              onPressed: _testingConnection ? null : _testConnection,
              loading: _testingConnection,
              expand: true,
            ),

            if (connTest.hasValue) ...[
              const SizedBox(height: AppSpacing.sm),
              Text(
                connTest.value == true
                    ? 'Ping succeeded — server is reachable.'
                    : 'Ping failed — check URL and network.',
                style: AppText.caption.copyWith(
                  color: connTest.value == true
                      ? AppColors.success
                      : AppColors.error,
                ),
              ),
            ],
            const SizedBox(height: AppSpacing.xs),
            Text(
              'Restart the app after changing the server URL.',
              style: AppText.caption,
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildSyncSection({
    required SettingsPrefsData prefs,
    required _SyncState syncState,
  }) {
    return LzSection(
      title: 'Sync',
      child: LzCard(
        padding: EdgeInsets.zero,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.lg,
                AppSpacing.lg,
                AppSpacing.lg,
                AppSpacing.sm,
              ),
              child: LzButton(
                label: syncState.running ? 'Syncing…' : 'Sync now',
                icon: Icons.sync,
                onPressed: syncState.running ? null : _syncNow,
                loading: syncState.running,
                expand: true,
              ),
            ),
            if (syncState.summary != null || syncState.error != null)
              Padding(
                padding: const EdgeInsets.symmetric(
                    horizontal: AppSpacing.lg),
                child: Text(
                  syncState.error != null
                      ? 'Error: ${syncState.error}'
                      : syncState.summary!,
                  style: AppText.caption.copyWith(
                    color: syncState.error != null
                        ? AppColors.error
                        : AppColors.success,
                  ),
                ),
              ),
            const SizedBox(height: AppSpacing.md),
            const Divider(height: 1, color: AppColors.borderSubtle),

            // Background-sync interval picker
            LzListTile(
              leading: const Icon(Icons.schedule,
                  size: 20, color: AppColors.textSecondary),
              title: 'Background sync',
              subtitle: 'How often to sync in the background',
              trailing: _IntervalPicker(
                value: prefs.syncInterval,
                onChanged: (v) async {
                  await ref
                      .read(settingsPrefsProvider.notifier)
                      .setSyncInterval(v);
                  await _rescheduleBackgroundSync(v);
                },
              ),
            ),
            const Divider(height: 1, color: AppColors.borderSubtle),

            // View conflicts
            LzListTile(
              leading: const Icon(Icons.compare_arrows,
                  size: 20, color: AppColors.textSecondary),
              title: 'View conflicts',
              subtitle: 'Inspect LWW sync conflicts and rejected creates',
              onTap: _viewConflicts,
              trailing: const Icon(Icons.chevron_right,
                  size: 18, color: AppColors.textMuted),
            ),
            const Divider(height: 1, color: AppColors.borderSubtle),

            // Wipe on logout
            _SwitchTile(
              icon: Icons.delete_sweep_outlined,
              title: 'Wipe cache on logout',
              subtitle: 'Clear all local data when you sign out',
              value: prefs.wipeOnLogout,
              onChanged: (v) => ref
                  .read(settingsPrefsProvider.notifier)
                  .setWipeOnLogout(v: v),
            ),
            const Divider(height: 1, color: AppColors.borderSubtle),

            // Clear cache now
            LzListTile(
              leading: const Icon(Icons.cleaning_services_outlined,
                  size: 20, color: AppColors.textSecondary),
              title: 'Clear local cache',
              subtitle: 'Removes all cached data immediately',
              onTap: _clearCache,
              trailing: const Icon(Icons.chevron_right,
                  size: 18, color: AppColors.textMuted),
            ),
          ],
        ),
      ),
    );
  }

  // ── Notification test/enable actions ──────────────────────────────────────

  /// Fire a REAL local notification to prove delivery. If notifications are
  /// turned off at the OS level, the SnackBar instead nudges the user to enable
  /// them (with an inline action that re-requests permission).
  Future<void> _sendTestNotification() async {
    final enabled = await LocalNotifications.areNotificationsEnabled();
    if (!mounted) return;
    if (!enabled) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: const Text('Notifications are off — enable them first.'),
          action: SnackBarAction(
            label: 'Enable',
            onPressed: () => LocalNotifications.requestPermissions(),
          ),
        ),
      );
      return;
    }
    await LocalNotifications.showTest();
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Test notification sent — check your shade.')),
    );
  }

  /// SCHEDULE a real reminder ~1 minute out (the same path task reminders use).
  /// This is the diagnostic for "the manual test works but my reminders never
  /// fire": if the immediate test arrives but this one doesn't, the OS is
  /// blocking exact alarms / killing the app in the background.
  Future<void> _scheduleTestReminder() async {
    final enabled = await LocalNotifications.areNotificationsEnabled();
    if (!mounted) return;
    if (!enabled) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: const Text('Notifications are off — enable them first.'),
          action: SnackBarAction(
            label: 'Enable',
            onPressed: () => LocalNotifications.requestPermissions(),
          ),
        ),
      );
      return;
    }
    final result = await LocalNotifications.scheduleTestReminder();
    if (!mounted) return;
    if (!result.ok) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          duration: const Duration(seconds: 12),
          content: Text('Could not schedule: ${result.error ?? "unknown error"}'),
        ),
      );
      return;
    }
    final hh = result.fire!.hour.toString().padLeft(2, '0');
    final mm = result.fire!.minute.toString().padLeft(2, '0');
    final base = 'Reminder scheduled for $hh:$mm (~1 min). Lock your phone and wait.';
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        duration: const Duration(seconds: 10),
        content: Text(result.exact
            ? base
            : '$base\n⚠ Exact alarms are OFF — scheduled inexactly (may be late). '
                'Tap Fix to allow them; HyperOS also needs Autostart + battery '
                'No-restrictions.'),
        action: result.exact
            ? null
            : SnackBarAction(
                label: 'Fix',
                onPressed: LocalNotifications.openExactAlarmSettings,
              ),
      ),
    );
  }

  /// Pick the time-of-day at which DATE-ONLY tasks remind. Persists the pref
  /// then reschedules every open task so the change takes effect immediately
  /// (the reminder service reads the updated default on each (re)schedule).
  Future<void> _pickDefaultReminderTime(int currentMinutes) async {
    final picked = await showTimePicker(
      context: context,
      initialTime: TimeOfDay(
        hour: currentMinutes ~/ 60,
        minute: currentMinutes % 60,
      ),
      builder: (ctx, child) => Theme(
        data: Theme.of(ctx).copyWith(
          colorScheme: ColorScheme.dark(
            primary: AppColors.accent,
            surface: AppColors.bgSurfaceElevated,
          ),
        ),
        child: child!,
      ),
    );
    if (picked == null || !mounted) return;
    final minutes = picked.hour * 60 + picked.minute;
    await ref.read(settingsPrefsProvider.notifier).setDefaultReminderMinutes(minutes);
    if (!mounted) return;
    // Reschedule existing tasks against the new default time-of-day. The
    // service already has the new value pushed in via ref.listen; syncAll
    // re-derives every fire time.
    final tasks = ref.read(tasksProvider).tasks;
    await ref.read(taskReminderServiceProvider).syncAll(tasks);
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          'Date-only tasks now remind at '
          '${formatClock12(picked.hour, picked.minute)}.',
        ),
      ),
    );
  }

  /// Delivery-channel segmented control (Telegram · App · Both) backed by
  /// `GET/POST /api/settings/notifications`, plus a one-line HyperOS hint.
  Widget _buildChannelTile() {
    final channelState = ref.watch(notificationChannelProvider);
    return Padding(
      padding: const EdgeInsets.all(AppSpacing.lg),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.podcasts_outlined,
                  size: 20, color: AppColors.textSecondary),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Delivery channel', style: AppText.body),
                    Text(
                      'Where watcher alerts, finished background jobs, and '
                      'escalations are sent',
                      style: AppText.caption,
                    ),
                  ],
                ),
              ),
              if (channelState.saving) ...[
                const SizedBox(width: AppSpacing.sm),
                const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
              ],
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          channelState.value.when(
            loading: () => const Padding(
              padding: EdgeInsets.symmetric(vertical: AppSpacing.sm),
              child: LinearProgressIndicator(),
            ),
            error: (e, _) => Text(
              'Could not load channel: $e',
              style: AppText.caption.copyWith(color: AppColors.error),
            ),
            data: (channel) => Wrap(
              spacing: AppSpacing.sm,
              runSpacing: AppSpacing.sm,
              children: [
                for (final c in NotificationChannel.values)
                  LzChip(
                    label: c.label,
                    selected: channel == c,
                    color: AppColors.accent,
                    dense: true,
                    onTap: channelState.saving || channel == c
                        ? null
                        : () async {
                            final err = await ref
                                .read(notificationChannelProvider.notifier)
                                .setChannel(c);
                            if (err != null && mounted) {
                              ScaffoldMessenger.of(context).showSnackBar(
                                SnackBar(content: Text('Notifications: $err')),
                              );
                            }
                          },
                  ),
              ],
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(
            'App delivery needs notifications allowed and — on HyperOS / MIUI — '
            'Autostart on + battery set to "No restrictions".',
            style: AppText.caption,
          ),
        ],
      ),
    );
  }

  Widget _buildNotificationsSection({required SettingsPrefsData prefs}) {
    return LzSection(
      title: 'Notifications',
      child: LzCard(
        padding: EdgeInsets.zero,
        child: Column(
          children: [
            // Permission status + one-tap enable (gates everything below).
            const _NotificationStatusTile(),
            const Divider(height: 1, color: AppColors.borderSubtle),
            // Fire a real notification right now to verify delivery.
            LzListTile(
              leading: const Icon(Icons.notifications_active_outlined,
                  size: 20, color: AppColors.textSecondary),
              title: 'Send test notification',
              subtitle: 'Fire a real notification now to verify delivery',
              onTap: _sendTestNotification,
              trailing: const Icon(Icons.chevron_right,
                  size: 18, color: AppColors.textMuted),
            ),
            const Divider(height: 1, color: AppColors.borderSubtle),
            // Schedule a real reminder ~1 min out — the SAME path task reminders
            // use. Diagnoses "manual test works but my reminders never fire":
            // if this one doesn't arrive, exact alarms / autostart are blocked.
            LzListTile(
              leading: const Icon(Icons.alarm_outlined,
                  size: 20, color: AppColors.textSecondary),
              title: 'Schedule test reminder',
              subtitle: 'Fires in ~1 min — verifies scheduled reminders work',
              onTap: _scheduleTestReminder,
              trailing: const Icon(Icons.chevron_right,
                  size: 18, color: AppColors.textMuted),
            ),
            const Divider(height: 1, color: AppColors.borderSubtle),
            // Delivery channel: where server-originated notifications (watchers,
            // background-job done, escalations) are sent.
            _buildChannelTile(),
            const Divider(height: 1, color: AppColors.borderSubtle),
            _SwitchTile(
              icon: Icons.chat_bubble_outline,
              title: 'Chat replies',
              subtitle: 'Notify when the agent replies to a message',
              value: prefs.notifyChatReply,
              onChanged: (v) => ref
                  .read(settingsPrefsProvider.notifier)
                  .setNotifyChatReply(v: v),
            ),
            const Divider(height: 1, color: AppColors.borderSubtle),
            _SwitchTile(
              icon: Icons.task_alt,
              title: 'Background tasks done',
              subtitle: 'Notify when a background task completes',
              value: prefs.notifyTaskDone,
              onChanged: (v) => ref
                  .read(settingsPrefsProvider.notifier)
                  .setNotifyTaskDone(v: v),
            ),
            const Divider(height: 1, color: AppColors.borderSubtle),
            _SwitchTile(
              icon: Icons.approval_outlined,
              title: 'Approvals',
              subtitle: 'Notify when the agent needs your approval',
              value: prefs.notifyApprovals,
              onChanged: (v) => ref
                  .read(settingsPrefsProvider.notifier)
                  .setNotifyApprovals(v: v),
            ),
            const Divider(height: 1, color: AppColors.borderSubtle),

            // Default reminder lead-time: applied automatically when a task
            // gains a due time without an explicit reminder.
            Padding(
              padding: const EdgeInsets.all(AppSpacing.lg),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const Icon(Icons.notifications_active_outlined,
                          size: 20, color: AppColors.textSecondary),
                      const SizedBox(width: AppSpacing.md),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text('Default reminder', style: AppText.body),
                            Text(
                              'Applied when a task with a due time has no '
                              'reminder set',
                              style: AppText.caption,
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: AppSpacing.sm),
                  Wrap(
                    spacing: AppSpacing.sm,
                    runSpacing: AppSpacing.sm,
                    children: [
                      for (final lead in _kLeadDefaultOptions)
                        LzChip(
                          label: lead.label,
                          selected: prefs.reminderLeadDefault == lead,
                          color: AppColors.accent,
                          dense: true,
                          onTap: () => ref
                              .read(settingsPrefsProvider.notifier)
                              .setReminderLeadDefault(lead),
                        ),
                    ],
                  ),
                ],
              ),
            ),
            const Divider(height: 1, color: AppColors.borderSubtle),

            // Default reminder TIME-OF-DAY: when a task has a due DATE but no
            // clock time ("due tomorrow"), its reminder fires at this time.
            // This is what makes ordinary date-only tasks actually remind.
            LzListTile(
              leading: const Icon(Icons.schedule_outlined,
                  size: 20, color: AppColors.textSecondary),
              title: 'Default reminder time',
              subtitle: 'Time of day a task with no clock time reminds',
              onTap: () => _pickDefaultReminderTime(prefs.defaultReminderMinutes),
              trailing: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    formatClock12(
                      prefs.defaultReminderMinutes ~/ 60,
                      prefs.defaultReminderMinutes % 60,
                    ),
                    style: AppText.body.copyWith(color: AppColors.textSecondary),
                  ),
                  const SizedBox(width: AppSpacing.xs),
                  const Icon(Icons.chevron_right,
                      size: 18, color: AppColors.textMuted),
                ],
              ),
            ),
            const Divider(height: 1, color: AppColors.borderSubtle),
            Padding(
              padding: const EdgeInsets.all(AppSpacing.md),
              child: Text(
                'Toggles are stored locally. Device-level permission must be '
                'granted in system settings. Task reminders fire at a task\'s '
                'reminder time (or its due time) even when the app is closed — '
                'on HyperOS / MIUI you must also allow notifications, enable '
                '"Alarms & reminders" (exact alarms), and turn on Autostart, '
                'or scheduled reminders are silently dropped.',
                style: AppText.caption,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildAndroidSection() {
    return LzSection(
      title: 'Android',
      child: LzCard(
        color: AppColors.bgSurfaceElevated,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.android,
                    color: AppColors.success, size: 18),
                const SizedBox(width: AppSpacing.sm),
                Text('Background battery settings', style: AppText.label),
              ],
            ),
            const SizedBox(height: AppSpacing.sm),
            Text(
              'For reliable background sync, set LazyClaw to "No restrictions" '
              'in battery settings. On HyperOS / MIUI, also enable Autostart.',
              style: AppText.caption,
            ),
            const SizedBox(height: AppSpacing.md),
            Wrap(
              spacing: AppSpacing.sm,
              runSpacing: AppSpacing.sm,
              children: [
                LzButton.secondary(
                  label: 'Battery settings',
                  icon: Icons.battery_saver_outlined,
                  onPressed: _openBatterySettings,
                ),
                LzButton.secondary(
                  label: 'Autostart',
                  icon: Icons.play_circle_outline,
                  onPressed: _openAutostartSettings,
                ),
                LzButton.secondary(
                  label: 'Home-screen widgets',
                  icon: Icons.widgets_outlined,
                  onPressed: _showWidgetHelp,
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  // ── Power tools link ─────────────────────────────────────────────────────

  Widget _buildPowerToolsSection() {
    return LzSection(
      title: 'Power tools',
      child: LzCard(
        padding: EdgeInsets.zero,
        child: LzListTile(
          leading: const Icon(Icons.bolt_outlined,
              size: 20, color: AppColors.accent),
          title: 'Power tools',
          subtitle: 'Skills, vault, memory, jobs, watchers, MCP…',
          onTap: () => context.push('/more'),
          trailing: const Icon(Icons.chevron_right,
              size: 18, color: AppColors.textMuted),
        ),
      ),
    );
  }

  // ── ECO mode section ──────────────────────────────────────────────────────

  Widget _buildEcoSection() {
    final ecoState = ref.watch(ecoProvider);
    return LzSection(
      title: 'Brain / ECO mode',
      child: ecoState.value.when(
        loading: () => const LzCard(
          child: SizedBox(
            height: 80,
            child: Center(child: CircularProgressIndicator()),
          ),
        ),
        error: (e, _) => LzBanner.error(
          message: e.toString(),
          safeAreaTop: false,
        ),
        data: (eco) => LzCard(
          padding: const EdgeInsets.all(AppSpacing.lg),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Mode chip row
              Wrap(
                spacing: AppSpacing.sm,
                runSpacing: AppSpacing.sm,
                children: eco.modes.map((m) {
                  final selected = m == eco.mode;
                  return LzChip(
                    label: m,
                    selected: selected,
                    onTap: ecoState.saving
                        ? null
                        : () async {
                            if (selected) return;
                            final err = await ref
                                .read(ecoProvider.notifier)
                                .setMode(m);
                            if (err != null && mounted) {
                              ScaffoldMessenger.of(context).showSnackBar(
                                SnackBar(
                                    content: Text('ECO: $err')),
                              );
                            }
                          },
                  );
                }).toList(),
              ),
              if (ecoState.saving) ...[
                const SizedBox(height: AppSpacing.sm),
                const LinearProgressIndicator(),
              ],
              const SizedBox(height: AppSpacing.md),
              // Brain / worker / fallback captions
              _EcoCaption(label: 'Brain', value: eco.brain),
              const SizedBox(height: AppSpacing.xs),
              _EcoCaption(label: 'Worker', value: eco.worker),
              const SizedBox(height: AppSpacing.xs),
              _EcoCaption(label: 'Fallback', value: eco.fallback),
            ],
          ),
        ),
      ),
    );
  }

  // ── Operating mode section (ADR-0005) ─────────────────────────────────────

  Widget _buildOperatingModeSection() {
    final modeState = ref.watch(agentModeProvider);
    return LzSection(
      title: 'Operating mode',
      child: modeState.value.when(
        loading: () => const LzCard(
          child: SizedBox(
            height: 80,
            child: Center(child: CircularProgressIndicator()),
          ),
        ),
        error: (e, _) => LzBanner.error(
          message: e.toString(),
          safeAreaTop: false,
        ),
        data: (current) => LzCard(
          padding: const EdgeInsets.all(AppSpacing.lg),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Wrap(
                spacing: AppSpacing.sm,
                runSpacing: AppSpacing.sm,
                children: kAgentModeLabels.entries.map((entry) {
                  final selected = entry.key == current;
                  return LzChip(
                    label: entry.value,
                    selected: selected,
                    onTap: modeState.saving
                        ? null
                        : () async {
                            if (selected) return;
                            final err = await ref
                                .read(agentModeProvider.notifier)
                                .setMode(entry.key);
                            if (err != null && mounted) {
                              ScaffoldMessenger.of(context).showSnackBar(
                                SnackBar(content: Text('Mode: $err')),
                              );
                            }
                          },
                  );
                }).toList(),
              ),
              if (modeState.saving) ...[
                const SizedBox(height: AppSpacing.sm),
                const LinearProgressIndicator(),
              ],
              const SizedBox(height: AppSpacing.md),
              Text(
                'Chat = talk only · Ask = confirm each action · '
                'Plan = research then Execute · Execute = autonomous',
                style: AppText.caption.copyWith(color: AppColors.textSecondary),
              ),
            ],
          ),
        ),
      ),
    );
  }

  // ── Permissions section ───────────────────────────────────────────────────

  Widget _buildPermissionsSection() {
    final permState = ref.watch(permissionsProvider);
    return LzSection(
      title: 'Permissions',
      child: permState.value.when(
        loading: () => const LzCard(
          child: SizedBox(
            height: 80,
            child: Center(child: CircularProgressIndicator()),
          ),
        ),
        error: (e, _) => LzBanner.error(
          message: e.toString(),
          safeAreaTop: false,
        ),
        data: (perms) {
          final categories = perms.categoryDefaults.entries.toList()
            ..sort((a, b) => a.key.compareTo(b.key));
          return LzCard(
            padding: EdgeInsets.zero,
            child: Column(
              children: [
                for (var i = 0; i < categories.length; i++) ...[
                  if (i > 0)
                    const Divider(height: 1, color: AppColors.borderSubtle),
                  _PermCategoryRow(
                    category: categories[i].key,
                    level: categories[i].value,
                    saving: permState.savingCategories
                        .contains(categories[i].key),
                    onChanged: (newLevel) async {
                      final err = await ref
                          .read(permissionsProvider.notifier)
                          .setCategory(categories[i].key, newLevel);
                      if (err != null && mounted) {
                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(
                              content:
                                  Text('Permissions: $err')),
                        );
                      }
                    },
                  ),
                ],
                if (categories.isEmpty)
                  Padding(
                    padding: const EdgeInsets.all(AppSpacing.lg),
                    child: Text(
                      'No categories configured.',
                      style: AppText.caption,
                    ),
                  ),
              ],
            ),
          );
        },
      ),
    );
  }

  /// Show the on-device captured uncaught-error log so a "random crash" trace
  /// can be read + copied to share. Empty = nothing crashed in Dart (a native
  /// crash or an OS force-kill leaves no Dart trace).
  Future<void> _showRecentErrors() async {
    final entries = await CrashLog.readAll();
    if (!mounted) return;
    final text = entries.isEmpty
        ? 'No errors captured. (A native plugin crash or an OS force-kill '
            'leaves no Dart trace — capture it with: adb logcat.)'
        : entries.map((e) => e.formatted).join('\n\n');
    await showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppColors.bgSurfaceElevated,
        title: Text('Recent errors (${entries.length})', style: AppText.title),
        content: SizedBox(
          width: double.maxFinite,
          child: SingleChildScrollView(
            child: SelectableText(
              text,
              style: AppText.caption.copyWith(
                color: AppColors.textSecondary,
                fontFamily: 'monospace',
              ),
            ),
          ),
        ),
        actions: [
          if (entries.isNotEmpty)
            TextButton(
              onPressed: () async {
                await CrashLog.clear();
                if (ctx.mounted) Navigator.of(ctx).pop();
              },
              child: Text('Clear', style: TextStyle(color: AppColors.error)),
            ),
          if (entries.isNotEmpty)
            TextButton(
              onPressed: () async {
                await Clipboard.setData(ClipboardData(text: text));
                if (!mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('Copied — paste it to share.')),
                );
              },
              child: Text('Copy', style: TextStyle(color: AppColors.accent)),
            ),
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: Text('Close',
                style: TextStyle(color: AppColors.textSecondary)),
          ),
        ],
      ),
    );
  }

  Widget _buildAboutSection() {
    return LzSection(
      title: 'About',
      child: LzCard(
        padding: EdgeInsets.zero,
        child: Column(
          children: [
            LzListTile(
              leading: const Icon(Icons.info_outline,
                  size: 20, color: AppColors.textSecondary),
              title: 'Version',
              trailing: Text(
                'v$kAppVersion ($kAppBuild)',
                style: AppText.caption,
              ),
            ),
            const Divider(height: 1, color: AppColors.borderSubtle),
            LzListTile(
              leading: const Icon(Icons.system_update_alt,
                  size: 20, color: AppColors.textSecondary),
              title: 'Check for update',
              subtitle: _checkingUpdate
                  ? 'Checking…'
                  : 'Download & install a newer APK',
              onTap: _checkingUpdate ? null : _checkForUpdate,
              trailing: _checkingUpdate
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.chevron_right,
                      size: 18, color: AppColors.textMuted),
            ),
            const Divider(height: 1, color: AppColors.borderSubtle),
            LzListTile(
              leading: const Icon(Icons.bug_report_outlined,
                  size: 20, color: AppColors.textSecondary),
              title: 'Recent errors',
              subtitle: 'Captured crash traces — tap to view & copy',
              onTap: _showRecentErrors,
              trailing: const Icon(Icons.chevron_right,
                  size: 18, color: AppColors.textMuted),
            ),
          ],
        ),
      ),
    );
  }
}

// ── Reusable sub-widgets ─────────────────────────────────────────────────────

/// A prominent, accent-styled banner shown at the TOP of Settings whenever
/// [updateAvailableProvider] holds a strictly-newer build. Tapping the banner —
/// or its trailing "Update" button — opens the existing [showUpdateDialog]
/// download/install flow. When no update is pending it renders nothing, so it
/// can sit unconditionally at the head of the settings list.
///
/// Extracted as a public widget (the "testable body") so the present/absent
/// behaviour can be pumped in isolation without the full screen's auth / eco /
/// permissions / secure-storage dependencies.
class SettingsUpdateTile extends ConsumerWidget {
  const SettingsUpdateTile({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pending = ref.watch(updateAvailableProvider);
    if (pending == null) return const SizedBox.shrink();

    void open() => showUpdateDialog(context, ref, pending);

    return Padding(
      key: const Key('settings-update-available'),
      padding: const EdgeInsets.only(bottom: AppSpacing.xl),
      child: LzCard(
        color: AppColors.accent.withValues(alpha: 0.14),
        borderColor: AppColors.accent,
        padding: EdgeInsets.zero,
        onTap: open,
        child: LzListTile(
          leading: const Icon(Icons.system_update_alt,
              size: 22, color: AppColors.accent),
          title: 'Update available',
          titleStyle: AppText.body.copyWith(
            color: AppColors.accent,
            fontWeight: FontWeight.w700,
          ),
          subtitle: 'v${pending.version} (build ${pending.build})',
          trailing: LzButton.primary(
            label: 'Update',
            icon: Icons.download,
            onPressed: open,
          ),
        ),
      ),
    );
  }
}

/// Notification permission status row: a colored dot + "Notifications: On/Off"
/// and — when OFF — an "Enable" action that re-requests OS permission, plus a
/// one-line HyperOS hint. Self-contained local state so it can refresh after a
/// permission prompt without rebuilding the whole settings screen.
class _NotificationStatusTile extends StatefulWidget {
  const _NotificationStatusTile();

  @override
  State<_NotificationStatusTile> createState() =>
      _NotificationStatusTileState();
}

class _NotificationStatusTileState extends State<_NotificationStatusTile> {
  bool? _enabled; // null = still checking
  bool _requesting = false;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    final enabled = await LocalNotifications.areNotificationsEnabled();
    if (!mounted) return;
    setState(() => _enabled = enabled);
  }

  Future<void> _enable() async {
    setState(() => _requesting = true);
    final granted = await LocalNotifications.requestPermissions();
    if (!mounted) return;
    setState(() {
      _enabled = granted;
      _requesting = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    final enabled = _enabled;
    final dot = enabled == null
        ? const LzStatusDot.muted()
        : enabled
            ? const LzStatusDot.success(glow: true)
            : const LzStatusDot.error();
    final label = enabled == null
        ? 'Notifications: checking…'
        : enabled
            ? 'Notifications: On'
            : 'Notifications: Off';

    return Padding(
      padding: const EdgeInsets.all(AppSpacing.lg),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.notifications_outlined,
                  size: 20, color: AppColors.textSecondary),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: Row(
                  children: [
                    dot,
                    const SizedBox(width: AppSpacing.sm),
                    Flexible(
                      child: Text(
                        label,
                        style: AppText.body,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
              ),
              if (enabled == false) ...[
                const SizedBox(width: AppSpacing.sm),
                LzButton.secondary(
                  label: _requesting ? 'Enabling…' : 'Enable',
                  icon: Icons.notifications_active_outlined,
                  loading: _requesting,
                  onPressed: _requesting ? null : _enable,
                ),
              ],
            ],
          ),
          if (enabled == false) ...[
            const SizedBox(height: AppSpacing.sm),
            Text(
              'If still off: Settings → Apps → LazyClaw → Notifications → Allow '
              '(HyperOS may also need Autostart + battery No-restrictions).',
              style: AppText.caption,
            ),
          ],
        ],
      ),
    );
  }
}

/// [LzListTile] row with a token-themed [Switch] trailing widget.
class _SwitchTile extends StatelessWidget {
  const _SwitchTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.value,
    required this.onChanged,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final bool value;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    return LzListTile(
      leading: Icon(icon, size: 20, color: AppColors.textSecondary),
      title: title,
      subtitle: subtitle,
      trailing: Switch(
        value: value,
        onChanged: onChanged,
        activeThumbColor: AppColors.accent,
        inactiveThumbColor: AppColors.textMuted,
        inactiveTrackColor: AppColors.bgSurfaceHover,
      ),
    );
  }
}

/// Inline [DropdownButton] to pick a [SyncInterval], styled for the dark theme.
class _IntervalPicker extends StatelessWidget {
  const _IntervalPicker({required this.value, required this.onChanged});

  final SyncInterval value;
  final ValueChanged<SyncInterval> onChanged;

  @override
  Widget build(BuildContext context) {
    return DropdownButton<SyncInterval>(
      value: value,
      dropdownColor: AppColors.bgSurfaceElevated,
      underline: const SizedBox.shrink(),
      style: AppText.caption.copyWith(color: AppColors.textPrimary),
      items: SyncInterval.values
          .map(
            (v) => DropdownMenuItem(
              value: v,
              child: Text(v.label),
            ),
          )
          .toList(),
      onChanged: (v) {
        if (v != null) onChanged(v);
      },
    );
  }
}

// ── ECO mode helper widgets ──────────────────────────────────────────────────

/// A muted key/value caption row for ECO brain/worker/fallback model names.
class _EcoCaption extends StatelessWidget {
  const _EcoCaption({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Text(
          '$label: ',
          style: AppText.caption.copyWith(
            color: AppColors.textMuted,
            fontWeight: FontWeight.w600,
          ),
        ),
        Expanded(
          child: Text(
            value.isNotEmpty ? value : '—',
            style: AppText.caption.copyWith(color: AppColors.textSecondary),
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }
}

// ── Home-screen widget help dialog step ───────────────────────────────────────

/// A single numbered step row inside the [_showWidgetHelp] dialog: an accent
/// number badge + wrapped instruction text.
class _HelpStep extends StatelessWidget {
  const _HelpStep({required this.n, required this.text});

  final String n;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          width: 22,
          height: 22,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: AppColors.accent.withValues(alpha: 0.16),
            borderRadius: AppRadii.rSm,
          ),
          child: Text(
            n,
            style: AppText.caption.copyWith(
              color: AppColors.accent,
              fontWeight: FontWeight.w700,
            ),
          ),
        ),
        const SizedBox(width: AppSpacing.md),
        Expanded(
          child: Text(
            text,
            style: AppText.caption.copyWith(color: AppColors.textSecondary),
          ),
        ),
      ],
    );
  }
}

// ── Permissions helper widget ─────────────────────────────────────────────────

const _kPermLevels = ['allow', 'ask', 'deny'];

/// The default-reminder-lead options offered in Settings → Notifications.
const _kLeadDefaultOptions = <ReminderLead>[
  ReminderLead.none,
  ReminderLead.atTime,
  ReminderLead.min10,
  ReminderLead.min30,
  ReminderLead.hour1,
  ReminderLead.day1,
];

/// One permissions category row: label on the left, allow/ask/deny chips on the
/// right. While [saving] is true the chips are disabled and a micro spinner
/// replaces the trailing area.
class _PermCategoryRow extends StatelessWidget {
  const _PermCategoryRow({
    required this.category,
    required this.level,
    required this.saving,
    required this.onChanged,
  });

  final String category;
  final String level;
  final bool saving;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      child: Row(
        children: [
          Expanded(
            child: Text(
              category.replaceAll('_', ' '),
              style: AppText.body,
            ),
          ),
          if (saving)
            const SizedBox(
              width: 18,
              height: 18,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          else
            Wrap(
              spacing: AppSpacing.xs,
              children: _kPermLevels.map((l) {
                final selected = l == level;
                Color? chipColor;
                if (l == 'allow') chipColor = AppColors.success;
                if (l == 'deny') chipColor = AppColors.error;
                return LzChip(
                  label: l,
                  selected: selected,
                  dense: true,
                  color: chipColor,
                  onTap: selected ? null : () => onChanged(l),
                );
              }).toList(),
            ),
        ],
      ),
    );
  }
}
