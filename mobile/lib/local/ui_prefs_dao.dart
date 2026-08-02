import 'dart:convert';

import 'package:sqflite_sqlcipher/sqflite.dart';

/// Tiny KV store for client-local UI state (collapse/expand, hide-completed).
/// Deliberately NOT synced — this is per-device preference, not user data.
class UiPrefsDao {
  final Database _db;
  UiPrefsDao(this._db);

  Future<String?> get(String key) async {
    final rows = await _db.query('ui_prefs',
        where: 'key = ?', whereArgs: [key], limit: 1);
    return rows.isEmpty ? null : rows.first['value'] as String?;
  }

  Future<void> set(String key, String value) => _db.insert(
      'ui_prefs', {'key': key, 'value': value},
      conflictAlgorithm: ConflictAlgorithm.replace);

  Future<bool> getBool(String key, {bool fallback = false}) async =>
      switch (await get(key)) { '1' => true, '0' => false, _ => fallback };

  Future<void> setBool(String key, bool value) => set(key, value ? '1' : '0');

  Future<Set<String>> getStringSet(String key) async {
    final raw = await get(key);
    if (raw == null || raw.isEmpty) return <String>{};
    try {
      final decoded = jsonDecode(raw);
      if (decoded is List) return decoded.map((e) => e.toString()).toSet();
    } catch (_) {}
    return <String>{};
  }

  Future<void> setStringSet(String key, Set<String> values) =>
      set(key, jsonEncode(values.toList()));
}
