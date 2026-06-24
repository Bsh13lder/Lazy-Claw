import 'dart:io';
import 'package:dio/dio.dart';
import 'package:dio_cookie_manager/dio_cookie_manager.dart';
import 'package:cookie_jar/cookie_jar.dart';
import 'package:path_provider/path_provider.dart';
import 'api_exceptions.dart';

class ApiClient {
  late final Dio _dio;
  late final PersistCookieJar _cookieJar;
  bool _initialized = false;

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
        // Skip ngrok's free-tier browser-warning interstitial so API calls go
        // straight through. Harmless on the LAN/gateway path (ignored there).
        'ngrok-skip-browser-warning': 'true',
      },
    ));

    _dio.interceptors.add(CookieManager(_cookieJar));
    _dio.interceptors.add(_ErrorInterceptor(on401: () => on401?.call()));

    _initialized = true;
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
