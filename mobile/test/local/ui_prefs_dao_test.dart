// UiPrefsDao: tiny client-local KV store for UI state (collapse/expand,
// hide-completed). Deliberately NOT synced. Same real in-memory SQLite (FFI)
// harness as the other DAO tests.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/ui_prefs_dao.dart';
import 'package:lazyclaw_mobile/providers/ui_prefs_provider.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

int _dbCounter = 0;

Future<UiPrefsDao> _freshDao() async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:uiprefsmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return UiPrefsDao(db);
}

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('UiPrefsDao', () {
    test('get on an absent key returns null', () async {
      final dao = await _freshDao();
      expect(await dao.get('missing'), isNull);
    });

    test('set/get round-trips a string value', () async {
      final dao = await _freshDao();
      await dao.set('collapsed_project_p1', 'true');
      expect(await dao.get('collapsed_project_p1'), 'true');
    });

    test('set overwrites a previous value for the same key', () async {
      final dao = await _freshDao();
      await dao.set('k', 'first');
      await dao.set('k', 'second');
      expect(await dao.get('k'), 'second');
    });

    test('getBool falls back to the default on an absent key', () async {
      final dao = await _freshDao();
      expect(await dao.getBool('hide_completed'), isFalse);
      expect(await dao.getBool('hide_completed', fallback: true), isTrue);
    });

    test('setBool/getBool round-trips true and false', () async {
      final dao = await _freshDao();
      await dao.setBool('hide_completed', true);
      expect(await dao.getBool('hide_completed'), isTrue);
      await dao.setBool('hide_completed', false);
      expect(await dao.getBool('hide_completed'), isFalse);
    });

    test('getStringSet on an absent key returns an empty set', () async {
      final dao = await _freshDao();
      expect(await dao.getStringSet('collapsed_projects'), isEmpty);
    });

    test('setStringSet/getStringSet round-trips via JSON', () async {
      final dao = await _freshDao();
      await dao.setStringSet('collapsed_projects', {'p1', 'p2', 'p3'});
      expect(await dao.getStringSet('collapsed_projects'),
          {'p1', 'p2', 'p3'});
    });

    test('setStringSet overwrite replaces the previous set entirely',
        () async {
      final dao = await _freshDao();
      await dao.setStringSet('collapsed_projects', {'p1', 'p2'});
      await dao.setStringSet('collapsed_projects', {'p3'});
      expect(await dao.getStringSet('collapsed_projects'), {'p3'});
    });

    // The Calendar view's "Show repeats" toggle (fix for the 2026-08
    // "every day says +37" ghost-noise regression) — default-ON, persisted
    // under kPrefCalendarShowRepeats exactly like every other Tasks-tab UI
    // pref.
    test('round-trips kPrefCalendarShowRepeats, defaulting to true (ON) '
        'when never set', () async {
      final dao = await _freshDao();
      expect(
        await dao.getBool(kPrefCalendarShowRepeats, fallback: true),
        isTrue,
      );

      await dao.setBool(kPrefCalendarShowRepeats, false);
      expect(
        await dao.getBool(kPrefCalendarShowRepeats, fallback: true),
        isFalse,
      );

      await dao.setBool(kPrefCalendarShowRepeats, true);
      expect(
        await dao.getBool(kPrefCalendarShowRepeats, fallback: true),
        isTrue,
      );
    });
  });
}
