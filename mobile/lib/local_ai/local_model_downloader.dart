/// On-device model download — the fallback to USB push.
///
/// Streams a `.gguf` straight from Hugging Face to the app's external models
/// dir using Dio (already a project dep), to a `.part` temp file that's renamed
/// into place only on a clean finish (so an interrupted download is never
/// mistaken for a ready model). One download at a time.
library;

import 'dart:io';

import 'package:dio/dio.dart';

import 'local_model.dart';
import 'local_model_store.dart';

class LocalModelDownloader {
  LocalModelDownloader(this._store);

  final LocalModelStore _store;
  final Dio _dio = Dio();
  CancelToken? _cancel;

  /// Download [model] to its on-disk location, reporting (received, total)
  /// bytes via [onProgress]. Throws on network error or cancellation.
  Future<void> download(
    LocalModel model, {
    required void Function(int received, int total) onProgress,
  }) async {
    final file = await _store.fileFor(model);
    final tmp = '${file.path}.part';
    // ignore: avoid_print
    print('LOCAL_DL: writing to $tmp');
    _cancel = CancelToken();
    try {
      await _dio.download(
        model.url,
        tmp,
        cancelToken: _cancel,
        deleteOnError: true,
        onReceiveProgress: onProgress,
        options: Options(
          followRedirects: true,
          maxRedirects: 8,
          receiveTimeout: const Duration(minutes: 30),
          headers: const {'User-Agent': 'LazyClaw/1.0 (Android)'},
        ),
      );
      if (await file.exists()) await file.delete();
      await File(tmp).rename(file.path);
    } catch (_) {
      final partial = File(tmp);
      if (await partial.exists()) {
        try {
          await partial.delete();
        } catch (_) {/* best effort */}
      }
      rethrow;
    }
  }

  void cancel() => _cancel?.cancel('cancelled by user');
}
