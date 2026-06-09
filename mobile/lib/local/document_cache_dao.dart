import 'dart:convert';
import 'dart:typed_data';

import 'package:sqflite_sqlcipher/sqflite.dart';

/// Total on-disk budget for the document read-through cache. When a write pushes
/// the cache over this, the least-recently-cached rows are evicted until it
/// fits. 64 MB comfortably holds many sheets/docs and a handful of PDFs without
/// letting the encrypted DB grow unbounded.
const int kDocumentCacheBudgetBytes = 64 * 1024 * 1024;

/// A cached document: a sheet/doc carries [payloadJson] (Univer snapshot), a PDF
/// carries [bytes]. Exactly one of the two is populated.
class CachedDoc {
  const CachedDoc({
    required this.kind,
    required this.id,
    required this.name,
    this.payloadJson,
    this.bytes,
    this.updatedAt,
  });

  final String kind;
  final String id;
  final String name;
  final String? payloadJson;
  final List<int>? bytes;
  final String? updatedAt;

  /// Decoded Univer snapshot (sheets/docs). Empty map when not a payload doc or
  /// the stored JSON is malformed.
  Map<String, dynamic> get payload {
    final raw = payloadJson;
    if (raw == null || raw.isEmpty) return const {};
    try {
      final decoded = jsonDecode(raw);
      return decoded is Map ? Map<String, dynamic>.from(decoded) : const {};
    } catch (_) {
      return const {};
    }
  }
}

/// Read-through cache for the office suite (Sheets / Docs / PDF).
///
/// This is a CACHE, not a sync source — it never enqueues to the outbox and has
/// no dirty/tombstone columns. Documents stay server-owned; edits go straight to
/// the server. The cache exists so a document opens INSTANTLY from disk while it
/// revalidates over the network (and so it stays viewable offline).
class DocumentCacheDao {
  DocumentCacheDao(this._db, {String Function()? now, int budgetBytes = kDocumentCacheBudgetBytes})
      : _now = now ?? (() => DateTime.now().toUtc().toIso8601String()),
        _budgetBytes = budgetBytes;

  final Database _db;
  final String Function() _now;
  final int _budgetBytes;

  static const _table = 'document_cache';
  static const _listTable = 'document_list_cache';

  /// Read a single cached document, or null on a miss.
  Future<CachedDoc?> getDoc(String kind, String id) async {
    final rows = await _db.query(
      _table,
      where: 'kind = ? AND id = ?',
      whereArgs: [kind, id],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    final r = rows.first;
    final rawBytes = r['bytes'];
    return CachedDoc(
      kind: r['kind'] as String,
      id: r['id'] as String,
      name: (r['name'] as String?) ?? 'Untitled',
      payloadJson: r['payload'] as String?,
      bytes: rawBytes is Uint8List
          ? rawBytes
          : (rawBytes is List ? List<int>.from(rawBytes) : null),
      updatedAt: r['updated_at'] as String?,
    );
  }

  /// Upsert a document into the cache, then enforce the byte budget. Pass
  /// [payloadJson] for sheets/docs or [bytes] for PDFs.
  Future<void> putDoc({
    required String kind,
    required String id,
    required String name,
    String? payloadJson,
    List<int>? bytes,
    String? updatedAt,
  }) async {
    final size = bytes?.length ?? (payloadJson == null ? 0 : utf8.encode(payloadJson).length);
    await _db.insert(
      _table,
      {
        'kind': kind,
        'id': id,
        'name': name,
        'payload': payloadJson,
        'bytes': bytes == null ? null : Uint8List.fromList(bytes),
        'updated_at': updatedAt,
        'byte_size': size,
        'cached_at': _now(),
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
    await _evictOverBudget();
  }

  /// Drop a single cached document (e.g. after the server confirms a delete).
  Future<void> deleteDoc(String kind, String id) async {
    await _db.delete(_table, where: 'kind = ? AND id = ?', whereArgs: [kind, id]);
  }

  /// The cached document index for [kind] (the list view), or null on a miss.
  Future<List<Map<String, dynamic>>?> getList(String kind) async {
    final rows = await _db.query(
      _listTable,
      where: 'kind = ?',
      whereArgs: [kind],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    final raw = rows.first['items'] as String?;
    if (raw == null || raw.isEmpty) return null;
    try {
      final decoded = jsonDecode(raw);
      if (decoded is! List) return null;
      return decoded
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e))
          .toList();
    } catch (_) {
      return null;
    }
  }

  /// Cache the document index for [kind].
  Future<void> putList(String kind, List<Map<String, dynamic>> items) async {
    await _db.insert(
      _listTable,
      {'kind': kind, 'items': jsonEncode(items), 'cached_at': _now()},
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  /// Sum of cached payload/byte sizes — exposed for eviction + tests.
  Future<int> totalBytes() async {
    final res = await _db.rawQuery('SELECT COALESCE(SUM(byte_size), 0) AS t FROM $_table');
    final v = res.first['t'];
    return v is int ? v : int.tryParse('$v') ?? 0;
  }

  /// Evict least-recently-cached rows until the cache fits the byte budget.
  Future<void> _evictOverBudget() async {
    if (await totalBytes() <= _budgetBytes) return;
    // Walk rows oldest-first, deleting until we're under budget.
    final rows = await _db.query(
      _table,
      columns: ['kind', 'id', 'byte_size'],
      orderBy: 'cached_at ASC',
    );
    var running = await totalBytes();
    for (final r in rows) {
      if (running <= _budgetBytes) break;
      await _db.delete(
        _table,
        where: 'kind = ? AND id = ?',
        whereArgs: [r['kind'], r['id']],
      );
      running -= (r['byte_size'] as int?) ?? 0;
    }
  }
}
