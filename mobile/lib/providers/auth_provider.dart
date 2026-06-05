import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api/api_client.dart';
import '../core/api/api_exceptions.dart';
import '../models/user.dart';
import '../repositories/auth_repository.dart';

enum AuthStatus { loading, authenticated, unauthenticated }

class AuthState {
  final AuthStatus status;
  final User? user;
  const AuthState(this.status, this.user);
  factory AuthState.loading() => const AuthState(AuthStatus.loading, null);
  factory AuthState.authenticated(User u) =>
      AuthState(AuthStatus.authenticated, u);
  factory AuthState.unauthenticated() =>
      const AuthState(AuthStatus.unauthenticated, null);
}

final apiClientProvider = Provider<ApiClient>((ref) {
  // baseUrl is resolved asynchronously by bootstrap (Task 13); default for now.
  return ApiClient(baseUrl: ref.watch(baseUrlProvider));
});

final baseUrlProvider = StateProvider<String>((ref) => 'http://127.0.0.1:18789');

final authRepositoryProvider = Provider<AuthRepository>((ref) =>
    AuthRepository(DioAuthTransport(ref.watch(apiClientProvider))));

class AuthNotifier extends StateNotifier<AuthState> {
  final AuthRepository _repo;
  AuthNotifier(this._repo) : super(AuthState.unauthenticated());

  void handle401() => state = AuthState.unauthenticated();

  Future<String?> login(String username, String password) async {
    try {
      final user = await _repo.login(username, password);
      state = AuthState.authenticated(user);
      return null;
    } on ApiError catch (e) {
      return e.message;
    } catch (e) {
      return e.toString();
    }
  }

  Future<String?> register(String username, String password,
      {String? displayName, String? inviteToken}) async {
    try {
      await _repo.register(username, password,
          displayName: displayName, inviteToken: inviteToken);
      return await login(username, password);
    } on ApiError catch (e) {
      return e.message;
    } catch (e) {
      return e.toString();
    }
  }

  Future<void> checkSession() async {
    state = AuthState.loading();
    try {
      final user = await _repo.me();
      state = AuthState.authenticated(user);
    } catch (_) {
      state = AuthState.unauthenticated();
    }
  }

  Future<void> logout() async {
    state = AuthState.unauthenticated();
  }
}

final authProvider =
    StateNotifierProvider<AuthNotifier, AuthState>((ref) {
  final notifier = AuthNotifier(ref.watch(authRepositoryProvider));
  // Wire the transport's 401 hook to force logout.
  ref.watch(apiClientProvider).on401 = notifier.handle401;
  return notifier;
});
