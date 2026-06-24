/// On-device storage for local GGUF models.
///
/// Models live in the app's **external files** dir
/// (`/sdcard/Android/data/<pkg>/files/models`). That location is special: the
/// `adb` shell user is FUSE-exempt so a model can be PUSHED there over USB from
/// the Mac, while the app itself reads it back with ZERO runtime permissions.
/// Internal app storage would need root/run-as to push into; true shared
/// storage would need the All-Files permission — this dir avoids both.
library;

import 'dart:io';

import 'package:path_provider/path_provider.dart';

import 'local_model.dart';

/// Presence + size of one catalog model on disk.
class LocalModelStatus {
  const LocalModelStatus({
    required this.model,
    required this.present,
    required this.sizeBytes,
  });

  final LocalModel model;
  final bool present;

  /// Actual on-disk size (0 when absent).
  final int sizeBytes;

  /// True when the file is present and within 2% of its expected size — guards
  /// against a half-pushed / interrupted transfer being treated as ready.
  bool get complete =>
      present && sizeBytes >= (model.sizeBytesApprox * 0.98).floor();
}

/// Filesystem operations for local models. No inference, no state — just paths.
class LocalModelStore {
  /// The directory holding model files, created on first use. On Android this
  /// is the app-specific external dir; elsewhere it falls back to app support.
  Future<Directory> modelsDir() async {
    final base = await getExternalStorageDirectory() ??
        await getApplicationSupportDirectory();
    final dir = Directory('${base.path}/models');
    if (!await dir.exists()) await dir.create(recursive: true);
    return dir;
  }

  /// The expected on-disk file for [model] (may not exist yet).
  Future<File> fileFor(LocalModel model) async {
    final dir = await modelsDir();
    return File('${dir.path}/${model.fileName}');
  }

  /// Absolute path to a present model file, or null when it isn't on disk.
  Future<String?> resolvePath(LocalModel model) async {
    final f = await fileFor(model);
    return await f.exists() ? f.path : null;
  }

  Future<LocalModelStatus> status(LocalModel model) async {
    final f = await fileFor(model);
    if (await f.exists()) {
      return LocalModelStatus(
        model: model,
        present: true,
        sizeBytes: await f.length(),
      );
    }
    return LocalModelStatus(model: model, present: false, sizeBytes: 0);
  }

  Future<List<LocalModelStatus>> listStatuses() async {
    return [for (final m in kLocalModels) await status(m)];
  }

  Future<void> delete(LocalModel model) async {
    final f = await fileFor(model);
    if (await f.exists()) await f.delete();
  }
}
