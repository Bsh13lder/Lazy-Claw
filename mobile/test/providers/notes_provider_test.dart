import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/note_dao.dart';
import 'package:lazyclaw_mobile/providers/notes_provider.dart';
import 'package:lazyclaw_mobile/repositories/notes_repository.dart';
import 'package:lazyclaw_mobile/sync/note_sync.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

/// Transport that always fails the network — proves the provider works fully
/// offline (writes land locally + dirty, list + search read from cache).
class _OfflineTransport implements NotesTransport {
  @override
  Future<Map<String, dynamic>> getJson(String path,
          {Map<String, dynamic>? queryParams}) async =>
      throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> postJson(
          String path, Map<String, dynamic> body) async =>
      throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> patchJson(
          String path, Map<String, dynamic> body) async =>
      throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async =>
      throw ApiError(0, 'offline');
}

int _dbCounter = 0;

Future<NoteDao> _freshDao() async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:noteprovmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return NoteDao(db);
}

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('NotesNotifier offline-first', () {
    test('createNote writes to cache + marks dirty while offline', () async {
      final dao = await _freshDao();
      final sync = NoteSync(dao, NotesRepository(_OfflineTransport()));
      final n = NotesNotifier(dao, sync);

      await n.createNote(title: 'Idea', content: 'Buy milk');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.notes.map((x) => x.content), contains('Buy milk'));
      final created = n.state.notes.firstWhere((x) => x.content == 'Buy milk');
      expect(n.state.dirtyIds, contains(created.id));
    });

    test('updateNote edits locally + stays dirty offline', () async {
      final dao = await _freshDao();
      final sync = NoteSync(dao, NotesRepository(_OfflineTransport()));
      final n = NotesNotifier(dao, sync);

      await n.createNote(content: 'Original');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final id = n.state.notes.first.id;

      await n.updateNote(id, content: 'Edited body');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.notes.firstWhere((x) => x.id == id).content, 'Edited body');
      expect(n.state.dirtyIds, contains(id));
    });

    test('deleteNote removes from the visible list offline', () async {
      final dao = await _freshDao();
      final sync = NoteSync(dao, NotesRepository(_OfflineTransport()));
      final n = NotesNotifier(dao, sync);

      await n.createNote(content: 'Remove me');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final id = n.state.notes.first.id;

      await n.deleteNote(id);
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.notes.map((x) => x.id), isNot(contains(id)));
    });

    test('load reads from cache instantly even with the server down', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate(content: 'Pre-existing', id: 'pre1');
      final sync = NoteSync(dao, NotesRepository(_OfflineTransport()));
      final n = NotesNotifier(dao, sync);

      await n.load();
      expect(n.state.notes.map((x) => x.content), contains('Pre-existing'));
      expect(n.state.isLoading, isFalse);
    });

    test('outbox accumulates the offline mutations for later replay', () async {
      final dao = await _freshDao();
      final sync = NoteSync(dao, NotesRepository(_OfflineTransport()));
      final n = NotesNotifier(dao, sync);

      await n.createNote(content: 'A');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final id = n.state.notes.first.id;
      await n.updateNote(id, title: 'A-titled');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      final outbox = await dao.readOutbox();
      expect(outbox.map((o) => o.op),
          containsAll([NoteOutboxOp.create, NoteOutboxOp.update]));
    });

    test('search filters the LOCAL cache (works offline)', () async {
      final dao = await _freshDao();
      final sync = NoteSync(dao, NotesRepository(_OfflineTransport()));
      final n = NotesNotifier(dao, sync);

      await n.createNote(title: 'Groceries', content: 'milk and eggs');
      await n.createNote(title: 'Work', content: 'finish the report');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      await n.search('report');
      expect(n.state.searchQuery, 'report');
      expect(n.state.notes.map((x) => x.title), ['Work']);

      // Tag + title also match.
      await n.search('grocer');
      expect(n.state.notes.map((x) => x.title), ['Groceries']);

      // Empty query restores the full list.
      await n.search('');
      expect(n.state.searchQuery, '');
      expect(n.state.notes, hasLength(2));
    });
  });
}
