import 'dart:convert';
import 'dart:io';

import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

/// One captured uncaught error.
class CrashEntry {
  final String ts;
  final String message;
  final String stack;
  const CrashEntry({required this.ts, required this.message, required this.stack});

  /// A copy-friendly multi-line rendering (timestamp + message + trimmed stack).
  String get formatted =>
      '[$ts] $message${stack.isEmpty ? '' : '\n$stack'}';
}

/// Lightweight on-device capture of uncaught errors, so an otherwise invisible
/// "the app just closed" can be inspected and shared instead of vanishing.
///
/// Backed by a single JSON-lines file in the app-support dir (no new deps —
/// `path` + `path_provider` are already used). Best-effort and EXTREMELY
/// defensive: every method swallows its own errors, because the whole point is
/// to run inside crash handlers where a second throw would be catastrophic.
///
/// NOTE: this only captures DART errors (uncaught exceptions, framework errors,
/// async errors). A native plugin crash or the OS force-killing the app for
/// memory/battery leaves nothing here — that needs `adb logcat`.
class CrashLog {
  CrashLog._();

  static const String _fileName = 'lazyclaw_errors.log';
  static const int _maxEntries = 40;
  static File? _file;

  static Future<File> _resolve() async {
    final cached = _file;
    if (cached != null) return cached;
    final dir = await getApplicationSupportDirectory();
    return _file = File(p.join(dir.path, _fileName));
  }

  /// Append one error entry (message + a trimmed stack). Never throws.
  static Future<void> record(Object error, [StackTrace? stack]) async {
    try {
      final entry = jsonEncode({
        'ts': DateTime.now().toIso8601String(),
        'message': error.toString(),
        'stack': stack == null
            ? ''
            : stack.toString().split('\n').take(14).join('\n'),
      });
      final file = await _resolve();
      final lines =
          await file.exists() ? await file.readAsLines() : <String>[];
      lines.add(entry);
      final capped = lines.length > _maxEntries
          ? lines.sublist(lines.length - _maxEntries)
          : lines;
      await file.writeAsString(capped.join('\n'));
    } catch (_) {
      // A logging failure must never crash the crash handler.
    }
  }

  /// All captured entries, most-recent first. Never throws.
  static Future<List<CrashEntry>> readAll() async {
    try {
      final file = await _resolve();
      if (!await file.exists()) return const [];
      final lines = await file.readAsLines();
      final out = <CrashEntry>[];
      for (final line in lines) {
        if (line.trim().isEmpty) continue;
        try {
          final m = jsonDecode(line) as Map<String, dynamic>;
          out.add(CrashEntry(
            ts: '${m['ts'] ?? ''}',
            message: '${m['message'] ?? ''}',
            stack: '${m['stack'] ?? ''}',
          ));
        } catch (_) {
          out.add(CrashEntry(ts: '', message: line, stack: ''));
        }
      }
      return out.reversed.toList();
    } catch (_) {
      return const [];
    }
  }

  /// Delete the captured log. Never throws.
  static Future<void> clear() async {
    try {
      final file = await _resolve();
      if (await file.exists()) await file.delete();
    } catch (_) {}
  }
}
