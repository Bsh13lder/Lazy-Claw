import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import '../constants/app_constants.dart';

class ServerConfig {
  static const _storage = FlutterSecureStorage();

  static String normalizeBaseUrl(String raw) {
    var v = raw.trim();
    if (!v.startsWith('http://') && !v.startsWith('https://')) {
      v = 'http://$v';
    }
    if (v.endsWith('/')) v = v.substring(0, v.length - 1);
    return v;
  }

  static String wsUrlFor(String baseUrl) {
    final b = normalizeBaseUrl(baseUrl);
    final ws = b.startsWith('https://')
        ? 'wss://${b.substring('https://'.length)}'
        : 'ws://${b.substring('http://'.length)}';
    return '$ws/ws/chat';
  }

  static Future<String> load() async =>
      (await _storage.read(key: kSecureBaseUrlKey)) ?? kDefaultBaseUrl;

  static Future<void> save(String baseUrl) async =>
      _storage.write(key: kSecureBaseUrlKey, value: normalizeBaseUrl(baseUrl));
}
