// Pure-function coverage of the router auth redirect, extracted from the
// GoRouter redirect closure so offline-mode routing is testable without the
// full screen graph. Both `authenticated` AND `authenticatedOffline` count as
// authed: a previously-logged-in user must reach the offline-first surfaces
// (Tasks/Notes/Budgets) when the server is unreachable.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/router/app_router.dart';
import 'package:lazyclaw_mobile/providers/auth_provider.dart';

void main() {
  test('loading never redirects (wait for auth to settle)', () {
    expect(authRedirect(AuthStatus.loading, loggingIn: false), isNull);
    expect(authRedirect(AuthStatus.loading, loggingIn: true), isNull);
  });

  test('unauthenticated outside auth screens -> /login', () {
    expect(
      authRedirect(AuthStatus.unauthenticated, loggingIn: false),
      '/login',
    );
  });

  test('unauthenticated on an auth screen stays put', () {
    expect(authRedirect(AuthStatus.unauthenticated, loggingIn: true), isNull);
  });

  test('authenticated on app screens stays put', () {
    expect(authRedirect(AuthStatus.authenticated, loggingIn: false), isNull);
  });

  test('authenticated on an auth screen -> /home', () {
    expect(authRedirect(AuthStatus.authenticated, loggingIn: true), '/home');
  });

  test('authenticatedOffline on app screens (e.g. /tasks) stays put', () {
    expect(
      authRedirect(AuthStatus.authenticatedOffline, loggingIn: false),
      isNull,
    );
  });

  test('authenticatedOffline on /login -> /home', () {
    expect(
      authRedirect(AuthStatus.authenticatedOffline, loggingIn: true),
      '/home',
    );
  });
}
