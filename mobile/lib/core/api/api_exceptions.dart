class ApiError implements Exception {
  final int status;
  final String message;

  ApiError(this.status, this.message);

  @override
  String toString() => 'ApiError($status): $message';

  bool get isUnauthorized => status == 401;
  bool get isNotFound => status == 404;
  bool get isForbidden => status == 403;
}
