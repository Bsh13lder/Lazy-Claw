/// Export helper: hand rendered document bytes to the OS share sheet.
///
/// The Documents suite is server-rendered + encrypted, so "export" means: fetch
/// the file bytes via the authed Dio client, write them to a temp file, and open
/// the platform share sheet (Save to Files / Drive / send). No storage
/// permission is needed — the share sheet handles the destination.
library;

import 'dart:io';

import 'package:path_provider/path_provider.dart';
import 'package:share_plus/share_plus.dart';

/// Sanitise a document name into a safe file stem (mirrors the server's
/// `_safe_filename`): keep alnum/dot/space/dash, collapse the rest to `_`.
String safeFileStem(String name) {
  final cleaned = name.replaceAll(RegExp(r'[^A-Za-z0-9._ -]'), '_').trim();
  final stem = cleaned.isEmpty ? 'document' : cleaned;
  return stem.length > 80 ? stem.substring(0, 80) : stem;
}

/// Write [bytes] to a temp file `<stem>.<ext>` and open the OS share sheet.
/// [mimeType] helps the OS route the file to the right apps.
Future<void> shareDocumentBytes({
  required List<int> bytes,
  required String stem,
  required String ext,
  required String mimeType,
}) async {
  final dir = await getTemporaryDirectory();
  final filename = '${safeFileStem(stem)}.$ext';
  final path = '${dir.path}/$filename';
  final file = File(path);
  await file.writeAsBytes(bytes, flush: true);
  await Share.shareXFiles(
    [XFile(path, mimeType: mimeType, name: filename)],
    subject: filename,
  );
}
