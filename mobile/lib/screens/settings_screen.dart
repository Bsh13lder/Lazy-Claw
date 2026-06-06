import 'dart:io' show Platform;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:workmanager/workmanager.dart';

import '../core/config/server_config.dart';
import '../core/constants/app_constants.dart';
import '../providers/auth_provider.dart';
import '../providers/budgets_provider.dart';
import '../providers/notes_provider.dart';
import '../providers/tasks_provider.dart';
import '../sync/background_sync.dart';
import '../sync/budgets_sync.dart';
import '../sync/note_sync.dart';
import '../sync/task_sync.dart';
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

  @override
  void initState() {
    super.initState();
    _urlController.text = ref.read(baseUrlProvider);
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
    try {
      final client = ref.read(apiClientProvider);
      final resp =
          await client.get<Map<String, dynamic>>('/api/mobile/version');
      final latest = resp['version']?.toString() ?? '';
      if (!mounted) return;
      if (latest.isEmpty || latest == kAppVersion) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('You are on the latest version.')),
        );
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Update available: v$latest')),
        );
      }
    } catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Could not check for updates — server unreachable.'),
        ),
      );
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

          // 6. Models / ECO mode (deferred — no mobile endpoints yet)
          _buildModelsSection(isReachable: isReachable),
          AppSpacing.vGap(AppSpacing.xl),

          // 7. About
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

  Widget _buildNotificationsSection({required SettingsPrefsData prefs}) {
    return LzSection(
      title: 'Notifications',
      child: LzCard(
        padding: EdgeInsets.zero,
        child: Column(
          children: [
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
            Padding(
              padding: const EdgeInsets.all(AppSpacing.md),
              child: Text(
                'Toggles are stored locally. '
                'Device-level permission must be granted in system settings.',
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
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildModelsSection({required bool isReachable}) {
    return LzSection(
      title: 'Models / ECO mode',
      child: LzCard(
        color: AppColors.bgSurfaceElevated,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.psychology_outlined,
                    size: 18, color: AppColors.textSecondary),
                const SizedBox(width: AppSpacing.sm),
                Text(
                  isReachable
                      ? 'Manage on the web for now'
                      : 'Server offline',
                  style: AppText.label
                      .copyWith(color: AppColors.textSecondary),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.xs),
            Text(
              'ECO mode, model selection, and permission rules need backend '
              'endpoints not yet exposed to the mobile client. '
              'Use the web UI to adjust these.',
              style: AppText.caption,
            ),
          ],
        ),
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
              subtitle: 'Pings /api/mobile/version',
              onTap: _checkForUpdate,
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
