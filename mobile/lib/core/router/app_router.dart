import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../providers/auth_provider.dart';
import '../../screens/login_screen.dart';
import '../../screens/register_screen.dart';
import '../../screens/chat_screen.dart';

/// A [ChangeNotifier] that fires whenever the [authProvider] state changes,
/// so [GoRouter.refreshListenable] re-evaluates the redirect immediately on
/// logout / 401 / login.
class _AuthListenable extends ChangeNotifier {
  _AuthListenable(Ref ref) {
    ref.listen<AuthState>(authProvider, (_, next) => notifyListeners());
  }
}

final routerProvider = Provider<GoRouter>((ref) {
  final listenable = _AuthListenable(ref);
  return GoRouter(
    initialLocation: '/chat',
    refreshListenable: listenable,
    redirect: (context, state) {
      final status = ref.read(authProvider).status;
      final loggingIn = state.matchedLocation == '/login' ||
          state.matchedLocation == '/register';
      if (status == AuthStatus.loading) return null;
      final authed = status == AuthStatus.authenticated;
      if (!authed && !loggingIn) return '/login';
      if (authed && loggingIn) return '/chat';
      return null;
    },
    routes: [
      GoRoute(path: '/login', builder: (ctx, _) => const LoginScreen()),
      GoRoute(path: '/register', builder: (ctx, _) => const RegisterScreen()),
      GoRoute(path: '/chat', builder: (ctx, _) => const ChatScreen()),
    ],
  );
});
