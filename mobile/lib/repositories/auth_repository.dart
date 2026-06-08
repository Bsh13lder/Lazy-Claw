import '../core/api/api_client.dart';
import '../models/user.dart';

/// Minimal seam so the repository is unit-testable without a live Dio.
abstract class AuthTransport {
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body);
}

class DioAuthTransport implements AuthTransport {
  final ApiClient _client;
  DioAuthTransport(this._client);
  @override
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body) {
    if (path == '/api/auth/me') {
      return _client.get<Map<String, dynamic>>(path,
          fromJson: (d) => Map<String, dynamic>.from(d as Map));
    }
    return _client.post<Map<String, dynamic>>(path, data: body,
        fromJson: (d) => Map<String, dynamic>.from(d as Map));
  }
}

class AuthRepository {
  final AuthTransport _t;
  AuthRepository(this._t);

  Future<User> login(String username, String password) async {
    final json = await _t.postJson('/api/auth/login',
        {'username': username, 'password': password});
    return User.fromJson(json);
  }

  Future<Map<String, dynamic>> register(
      String username, String password,
      {String? displayName, String? inviteToken}) async {
    return _t.postJson('/api/auth/register', {
      'username': username,
      'password': password,
      'display_name': displayName,
      'invite_token': inviteToken,
    });
  }

  Future<User> me() async {
    final json = await _t.postJson('/api/auth/me', const {});
    return User.fromJson(json);
  }
}
