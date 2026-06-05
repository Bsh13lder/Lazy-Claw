import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../providers/auth_provider.dart';
import '../../screens/chat_screen.dart';
import '../../screens/expenses_screen.dart';
import '../../screens/home_screen.dart';
import '../../screens/login_screen.dart';
import '../../screens/notes_screen.dart';
import '../../screens/register_screen.dart';
import '../../screens/settings_screen.dart';
import '../../screens/tasks_screen.dart';
import '../../ui/components/lz_bottom_nav.dart';

/// A [ChangeNotifier] that fires whenever the [authProvider] state changes,
/// so [GoRouter.refreshListenable] re-evaluates the redirect immediately on
/// logout / 401 / login.
class _AuthListenable extends ChangeNotifier {
  _AuthListenable(Ref ref) {
    ref.listen<AuthState>(authProvider, (_, next) => notifyListeners());
  }
}

// ── Tab configuration ──────────────────────────────────────────────────────
//
// 6 destinations: Home · Chat · Tasks · Money(expenses) · Notes · Settings.
// The branch order here MUST match the order of [StatefulShellBranch]es below
// and the [LzNavDestination]s in [_ShellScaffold].

const _tabs = <_Tab>[
  _Tab(path: '/home', label: 'Home', icon: Icons.home_outlined, activeIcon: Icons.home),
  _Tab(path: '/chat', label: 'Chat', icon: Icons.chat_bubble_outline, activeIcon: Icons.chat_bubble),
  _Tab(path: '/tasks', label: 'Tasks', icon: Icons.check_circle_outline, activeIcon: Icons.check_circle),
  _Tab(path: '/expenses', label: 'Money', icon: Icons.account_balance_wallet_outlined, activeIcon: Icons.account_balance_wallet),
  _Tab(path: '/notes', label: 'Notes', icon: Icons.notes_outlined, activeIcon: Icons.notes),
  _Tab(path: '/settings', label: 'Settings', icon: Icons.settings_outlined, activeIcon: Icons.settings),
];

class _Tab {
  final String path;
  final String label;
  final IconData icon;
  final IconData activeIcon;
  const _Tab({
    required this.path,
    required this.label,
    required this.icon,
    required this.activeIcon,
  });
}

// ── Shell scaffold ─────────────────────────────────────────────────────────

class _ShellScaffold extends StatelessWidget {
  final StatefulNavigationShell shell;
  const _ShellScaffold({required this.shell});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: shell,
      bottomNavigationBar: LzBottomNav(
        currentIndex: shell.currentIndex,
        onSelected: (index) => shell.goBranch(
          index,
          // Re-open the same branch route instead of always going to initial.
          initialLocation: index == shell.currentIndex,
        ),
        destinations: _tabs
            .map(
              (t) => LzNavDestination(
                label: t.label,
                icon: t.icon,
                activeIcon: t.activeIcon,
              ),
            )
            .toList(),
      ),
    );
  }
}

// ── Router provider ────────────────────────────────────────────────────────

final routerProvider = Provider<GoRouter>((ref) {
  final listenable = _AuthListenable(ref);
  return GoRouter(
    initialLocation: '/home',
    refreshListenable: listenable,
    redirect: (context, state) {
      final status = ref.read(authProvider).status;
      final loggingIn = state.matchedLocation == '/login' ||
          state.matchedLocation == '/register';
      if (status == AuthStatus.loading) return null;
      final authed = status == AuthStatus.authenticated;
      if (!authed && !loggingIn) return '/login';
      if (authed && loggingIn) return '/home';
      return null;
    },
    routes: [
      // Auth routes — OUTSIDE the shell so they have no bottom nav bar.
      GoRoute(path: '/login', builder: (ctx, _) => const LoginScreen()),
      GoRoute(path: '/register', builder: (ctx, _) => const RegisterScreen()),

      // Main shell — StatefulShellRoute.indexedStack keeps each branch alive
      // (the ChatScreen WebSocket connection survives tab switches).
      StatefulShellRoute.indexedStack(
        builder: (context, state, shell) => _ShellScaffold(shell: shell),
        branches: [
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/home',
                builder: (ctx, _) => const HomeScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/chat',
                builder: (ctx, _) => const ChatScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/tasks',
                builder: (ctx, _) => const TasksScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/expenses',
                builder: (ctx, _) => const ExpensesScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/notes',
                builder: (ctx, _) => const NotesScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/settings',
                builder: (ctx, _) => const SettingsScreen(),
              ),
            ],
          ),
        ],
      ),
    ],
  );
});
