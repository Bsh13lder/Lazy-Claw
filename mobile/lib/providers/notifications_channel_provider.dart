import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../repositories/notifications_repository.dart';
import '../screens/settings/settings_prefs.dart';
import 'auth_provider.dart';

/// UI state for the notification-delivery-channel control: the current value
/// (async) plus a `saving` flag while a POST is in flight. Mirrors [EcoState].
class NotificationChannelState {
  final AsyncValue<NotificationChannel> value;
  final bool saving;

  const NotificationChannelState({required this.value, this.saving = false});

  NotificationChannelState copyWith({
    AsyncValue<NotificationChannel>? value,
    bool? saving,
  }) =>
      NotificationChannelState(
        value: value ?? this.value,
        saving: saving ?? this.saving,
      );
}

/// Loads + persists the server notification-delivery channel
/// (`telegram` | `app` | `both`). Seeds from the local cache for an instant
/// render, then reconciles with the server. On set, writes through to the
/// server and updates the local cache.
class NotificationChannelNotifier
    extends StateNotifier<NotificationChannelState> {
  final NotificationsRepository _repo;

  NotificationChannelNotifier(this._repo)
      : super(const NotificationChannelState(value: AsyncValue.loading())) {
    _load();
  }

  Future<void> _load() async {
    // Optimistic seed from the local cache so the control isn't stuck spinning.
    final cached = await SettingsPrefs.loadNotificationChannelCache();
    if (cached != null) {
      state = state.copyWith(
        value: AsyncValue.data(NotificationChannel.fromWire(cached)),
      );
    }
    try {
      final channel = await _repo.getChannel();
      await SettingsPrefs.saveNotificationChannelCache(channel.wire);
      state = state.copyWith(value: AsyncValue.data(channel));
    } catch (e, st) {
      // Keep the cached value if we have one; only surface an error otherwise.
      if (state.value.hasValue) return;
      state = state.copyWith(value: AsyncValue.error(e, st));
    }
  }

  /// Persist a new channel. Returns null on success, else an error message.
  Future<String?> setChannel(NotificationChannel channel) async {
    state = state.copyWith(saving: true);
    try {
      final confirmed = await _repo.setChannel(channel);
      await SettingsPrefs.saveNotificationChannelCache(confirmed.wire);
      state = state.copyWith(value: AsyncValue.data(confirmed), saving: false);
      return null;
    } catch (e) {
      state = state.copyWith(saving: false);
      return e.toString();
    }
  }

  void reload() {
    state = state.copyWith(value: const AsyncValue.loading());
    _load();
  }
}

final notificationChannelProvider = StateNotifierProvider.autoDispose<
    NotificationChannelNotifier, NotificationChannelState>(
  (ref) => NotificationChannelNotifier(
    NotificationsRepository(
      DioNotificationsTransport(ref.watch(apiClientProvider)),
    ),
  ),
);
