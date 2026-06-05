import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/auth_provider.dart';
import 'package:lazyclaw_mobile/repositories/auth_repository.dart';
import 'package:lazyclaw_mobile/models/user.dart';

class _OkTransport implements AuthTransport {
  @override
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body) async =>
      {'id': 'u1', 'username': 'sam', 'role': 'user'};
}

void main() {
  test('login moves unauthenticated -> authenticated', () async {
    final notifier = AuthNotifier(AuthRepository(_OkTransport()));
    expect(notifier.state.status, AuthStatus.unauthenticated);
    final err = await notifier.login('sam', 'pw12345678');
    expect(err, isNull);
    expect(notifier.state.status, AuthStatus.authenticated);
    expect(notifier.state.user, isA<User>());
  });

  test('handle401 forces logout', () {
    final notifier = AuthNotifier(AuthRepository(_OkTransport()));
    notifier.state = AuthState.authenticated(
        const User(id: 'u1', username: 'sam', role: 'user'));
    notifier.handle401();
    expect(notifier.state.status, AuthStatus.unauthenticated);
  });
}
