import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/auth_repository.dart';
import 'package:lazyclaw_mobile/models/user.dart';

class _FakeTransport implements AuthTransport {
  Map<String, dynamic>? lastBody;
  String? lastPath;
  final Map<String, dynamic> response;
  _FakeTransport(this.response);
  @override
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body) async {
    lastPath = path; lastBody = body; return response;
  }
}

void main() {
  test('login posts credentials and parses the user', () async {
    final t = _FakeTransport(
        {'id': 'u1', 'username': 'sam', 'display_name': 'Sam', 'role': 'user'});
    final repo = AuthRepository(t);
    final result = await repo.login('sam', 'pw12345678');
    expect(t.lastPath, '/api/auth/login');
    expect(t.lastBody, {'username': 'sam', 'password': 'pw12345678'});
    expect(result, isA<User>());
    expect(result.username, 'sam');
  });
}
