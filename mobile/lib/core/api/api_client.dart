import 'dart:io';
import 'package:dio/dio.dart';
import 'package:dio_cookie_manager/dio_cookie_manager.dart';
import 'package:cookie_jar/cookie_jar.dart';
import 'package:path_provider/path_provider.dart';
import '../auth/session_token_store.dart';
import '../constants/app_constants.dart';
import 'api_exceptions.dart';
import 'session_cookie_interceptor.dart';

class ApiClient {
  late final Dio _dio;
  late final PersistCookieJar _cookieJar;
  bool _initialized = false;

  /// Host-agnostic session store, shared by every [ApiClient] (foreground + the
  /// background-sync isolate's own clients). Swappable in tests. See
  /// [SessionCookieInterceptor] for why the session can't rely on the per-host
  /// cookie jar alone.
  static SessionTokenStore sessionStore = const SecureSessionTokenStore();

  final String baseUrl;
  void Function()? on401;

  ApiClient({required this.baseUrl});

  Future<void> _ensureInitialized() async {
    if (_initialized) return;

    final dir = await getApplicationDocumentsDirectory();
    final cookieDir = '${dir.path}/.cookies/';
    await Directory(cookieDir).create(recursive: true);

    _cookieJar = PersistCookieJar(
      ignoreExpires: true,
      storage: FileStorage(cookieDir),
    );

    _dio = Dio(BaseOptions(
      baseUrl: baseUrl,
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 30),
      headers: {
        'Content-Type': 'application/json',
      },
    ));

    _dio.interceptors.add(CookieManager(_cookieJar));
    // Migrate any session the login already left in the per-host jar into the
    // host-agnostic store, then attach the interceptor that re-sends it to
    // whatever host the app is currently using. Order matters: this runs AFTER
    // CookieManager so same-host cookies still flow and we only fill the gap.
    await _bootstrapSessionToken();
    final token = await ApiClient.sessionStore.load();
    _dio.interceptors
        .add(SessionCookieInterceptor(ApiClient.sessionStore, initialToken: token));
    _dio.interceptors.add(_ErrorInterceptor(on401: () => on401?.call()));

    _initialized = true;
  }

  /// One-time migration: if the host-agnostic store has no session yet, look for
  /// a `session_id` the login left in the cookie jar under ANY host this server
  /// is reachable as, and copy it into the store. This makes the host-agnostic
  /// session take effect for an ALREADY-logged-in user without forcing a
  /// re-login after the update. Best-effort — never throws.
  Future<void> _bootstrapSessionToken() async {
    try {
      if (await ApiClient.sessionStore.load() != null) return;
      final candidates = <String>{
        baseUrl,
        kDefaultBaseUrl,
        kLanFallbackBaseUrl,
        kLanFallbackIpBaseUrl,
      };
      for (final url in candidates) {
        try {
          final cookies = await _cookieJar.loadForRequest(Uri.parse(url));
          for (final c in cookies) {
            if (c.name == SessionCookieInterceptor.cookieName &&
                c.value.isNotEmpty) {
              await ApiClient.sessionStore.save(c.value);
              return;
            }
          }
        } catch (_) {
          // Try the next candidate host.
        }
      }
    } catch (_) {
      // Bootstrap is best-effort; absence just means we wait for the next login.
    }
  }

  Future<T> get<T>(
    String path, {
    Map<String, dynamic>? queryParams,
    T Function(dynamic)? fromJson,
  }) async {
    await _ensureInitialized();
    final response = await _dio.get(path, queryParameters: queryParams);
    if (fromJson != null) return fromJson(response.data);
    return response.data as T;
  }

  Future<T> post<T>(
    String path, {
    dynamic data,
    T Function(dynamic)? fromJson,
  }) async {
    await _ensureInitialized();
    final response = await _dio.post(path, data: data);
    if (fromJson != null) return fromJson(response.data);
    return response.data as T;
  }

  Future<T> patch<T>(
    String path, {
    dynamic data,
    T Function(dynamic)? fromJson,
  }) async {
    await _ensureInitialized();
    final response = await _dio.patch(path, data: data);
    if (fromJson != null) return fromJson(response.data);
    return response.data as T;
  }

  Future<T> put<T>(
    String path, {
    dynamic data,
    T Function(dynamic)? fromJson,
  }) async {
    await _ensureInitialized();
    final response = await _dio.put(path, data: data);
    if (fromJson != null) return fromJson(response.data);
    return response.data as T;
  }

  Future<T> delete<T>(
    String path, {
    T Function(dynamic)? fromJson,
  }) async {
    await _ensureInitialized();
    final response = await _dio.delete(path);
    if (fromJson != null) return fromJson(response.data);
    return response.data as T;
  }

  Future<T> uploadFile<T>(
    String path,
    File file, {
    String fieldName = 'file',
    Map<String, dynamic>? extraFields,
    T Function(dynamic)? fromJson,
  }) async {
    await _ensureInitialized();
    final formData = FormData.fromMap({
      fieldName: await MultipartFile.fromFile(file.path, filename: file.path.split('/').last),
      if (extraFields != null) ...extraFields,
    });
    final response = await _dio.post(path, data: formData);
    if (fromJson != null) return fromJson(response.data);
    return response.data as T;
  }

  Future<Response> downloadFile(String path) async {
    await _ensureInitialized();
    return _dio.get(path, options: Options(responseType: ResponseType.bytes));
  }

  /// POST with JSON body, receive binary response (e.g. TTS audio).
  Future<List<int>> postForBytes(String path, {dynamic data}) async {
    await _ensureInitialized();
    final response = await _dio.post(
      path,
      data: data,
      options: Options(responseType: ResponseType.bytes),
    );
    return response.data as List<int>;
  }

  /// Get the session cookie value for use in WebSocket connections etc.
  Future<String?> getSessionCookie() async {
    await _ensureInitialized();
    final uri = Uri.parse(baseUrl);
    final cookies = await _cookieJar.loadForRequest(uri);
    for (final cookie in cookies) {
      if (cookie.name == 'session_id') return cookie.value;
    }
    return null;
  }

  Future<void> clearSession() async {
    await _ensureInitialized();
    await _cookieJar.deleteAll();
    // Drop the host-agnostic session too, else a logged-out app would keep
    // re-attaching a dead token to every request.
    try {
      await ApiClient.sessionStore.clear();
    } catch (_) {
      // Best-effort — a failing secure-storage delete must not block logout.
    }
  }
}

class _ErrorInterceptor extends Interceptor {
  final void Function()? on401;
  _ErrorInterceptor({this.on401});

  @override
  void onError(DioException err, ErrorInterceptorHandler handler) {
    final statusCode = err.response?.statusCode ?? 0;
    final data = err.response?.data;
    String message;
    if (data is Map && data.containsKey('detail')) {
      message = data['detail'].toString();
    } else if (data is String && data.isNotEmpty) {
      message = data;
    } else {
      message = err.message ?? 'Network error';
    }
    // Auto-logout on 401 (session expired)
    if (statusCode == 401 && on401 != null) {
      on401!();
    }
    handler.reject(DioException(
      requestOptions: err.requestOptions,
      response: err.response,
      error: ApiError(statusCode, message),
    ));
  }
}
