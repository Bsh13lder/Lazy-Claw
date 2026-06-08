import 'dart:io';

import '../core/api/api_client.dart';

// ── Kinds ────────────────────────────────────────────────────────────────────

/// The three document families the Documents tab surfaces. Each maps to its own
/// `/api/<api>` prefix and to a slightly different list/create/open shape.
enum DocKind { sheets, docs, pdf }

extension DocKindApi on DocKind {
  /// URL prefix segment (`sheets` / `docs` / `pdf`).
  String get api {
    switch (this) {
      case DocKind.sheets:
        return 'sheets';
      case DocKind.docs:
        return 'docs';
      case DocKind.pdf:
        return 'pdf';
    }
  }

  /// Singular wrapper key the create/get routes nest the row under
  /// (`{sheet|doc|file}`). PDFs use `file`.
  String get itemKey {
    switch (this) {
      case DocKind.sheets:
        return 'sheet';
      case DocKind.docs:
        return 'doc';
      case DocKind.pdf:
        return 'file';
    }
  }

  /// Plural key the list route returns the array under
  /// (`{sheets|docs|files}`).
  String get listKey {
    switch (this) {
      case DocKind.sheets:
        return 'sheets';
      case DocKind.docs:
        return 'docs';
      case DocKind.pdf:
        return 'files';
    }
  }

  String get label {
    switch (this) {
      case DocKind.sheets:
        return 'Sheets';
      case DocKind.docs:
        return 'Docs';
      case DocKind.pdf:
        return 'PDF';
    }
  }
}

// ── Models ───────────────────────────────────────────────────────────────────

/// One row in a document list — index shape only (no payload/bytes).
class DocMeta {
  const DocMeta({
    required this.id,
    required this.name,
    this.createdAt,
    this.updatedAt,
    this.pages,
  });

  final String id;
  final String name;
  final String? createdAt;
  final String? updatedAt;

  /// PDF page count (null for sheets/docs).
  final int? pages;

  factory DocMeta.fromJson(Map<String, dynamic> json) => DocMeta(
        id: json['id']?.toString() ?? '',
        name: (json['name']?.toString().trim().isNotEmpty ?? false)
            ? json['name'].toString()
            : 'Untitled',
        createdAt: json['created_at']?.toString(),
        updatedAt: json['updated_at']?.toString(),
        pages: json['pages'] is int
            ? json['pages'] as int
            : int.tryParse(json['pages']?.toString() ?? ''),
      );
}

/// A sheet/doc opened for viewing: metadata + its decrypted Univer snapshot.
class DocPayload {
  const DocPayload({required this.id, required this.name, required this.payload});

  final String id;
  final String name;

  /// Univer `IWorkbookData` (sheets) or `IDocumentData` (docs).
  final Map<String, dynamic> payload;

  factory DocPayload.fromJson(Map<String, dynamic> json) => DocPayload(
        id: json['id']?.toString() ?? '',
        name: json['name']?.toString() ?? 'Untitled',
        payload: json['payload'] is Map
            ? Map<String, dynamic>.from(json['payload'] as Map)
            : const {},
      );
}

/// The result of a ✨ AI edit. For sheets/docs [snapshot] carries the fresh
/// Univer payload; for PDFs [newPdfId] points at the new (immutable) file.
class AiEditResult {
  const AiEditResult({
    required this.ok,
    this.summary,
    this.snapshot,
    this.newPdfId,
    this.error,
  });

  final bool ok;
  final String? summary;
  final Map<String, dynamic>? snapshot;
  final String? newPdfId;
  final String? error;

  factory AiEditResult.fromJson(Map<String, dynamic> json) => AiEditResult(
        ok: json['ok'] == true,
        summary: json['summary']?.toString(),
        snapshot: json['snapshot'] is Map
            ? Map<String, dynamic>.from(json['snapshot'] as Map)
            : null,
        newPdfId: json['new_pdf_id']?.toString(),
        error: json['error']?.toString(),
      );
}

// ── Transport seam ───────────────────────────────────────────────────────────

/// Testable transport seam — mirrors the pattern from the other repos. Only the
/// verbs the [DocumentsRepository] needs are declared.
abstract class DocumentsTransport {
  Future<Map<String, dynamic>> getJson(String path);
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body);
  Future<Map<String, dynamic>> putJson(String path, Map<String, dynamic> body);
  Future<Map<String, dynamic>> deleteJson(String path);

  /// Multipart upload of [file] → decoded JSON (PDF import).
  Future<Map<String, dynamic>> uploadFile(String path, File file);

  /// Raw bytes (PDF `/raw` for the viewer).
  Future<List<int>> getBytes(String path);

  /// POST a JSON [body] and receive a binary response (encrypted export).
  Future<List<int>> postBytes(String path, Map<String, dynamic> body);
}

/// Dio-backed production transport over the shared [ApiClient].
class DioDocumentsTransport implements DocumentsTransport {
  final ApiClient _client;
  DioDocumentsTransport(this._client);

  Map<String, dynamic> _map(dynamic d) => Map<String, dynamic>.from(d as Map);

  @override
  Future<Map<String, dynamic>> getJson(String path) =>
      _client.get<Map<String, dynamic>>(path, fromJson: _map);

  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) =>
      _client.post<Map<String, dynamic>>(path, data: body, fromJson: _map);

  @override
  Future<Map<String, dynamic>> putJson(
    String path,
    Map<String, dynamic> body,
  ) =>
      _client.put<Map<String, dynamic>>(path, data: body, fromJson: _map);

  @override
  Future<Map<String, dynamic>> deleteJson(String path) =>
      _client.delete<Map<String, dynamic>>(path, fromJson: _map);

  @override
  Future<Map<String, dynamic>> uploadFile(String path, File file) =>
      _client.uploadFile<Map<String, dynamic>>(path, file, fromJson: _map);

  @override
  Future<List<int>> getBytes(String path) async {
    final res = await _client.downloadFile(path);
    return List<int>.from(res.data as List);
  }

  @override
  Future<List<int>> postBytes(String path, Map<String, dynamic> body) =>
      _client.postForBytes(path, data: body);
}

// ── Repository ───────────────────────────────────────────────────────────────

/// Network-only repository for the encrypted office suite (sheets / docs / pdf).
///
/// These files are server-owned and decrypted server-side per request, so —
/// like Chat and the power surfaces — the Documents tab is online-only (no
/// offline cache). One facade covers all three kinds; the [DocKind] selects the
/// `/api/<kind>` prefix and the wrapper keys.
class DocumentsRepository {
  final DocumentsTransport _t;
  DocumentsRepository(this._t);

  /// List a kind's items (index only). Maps `GET /api/<kind>` →
  /// `{ <listKey>: [...] }`.
  Future<List<DocMeta>> list(DocKind kind) async {
    final json = await _t.getJson('/api/${kind.api}');
    final raw = json[kind.listKey] as List? ?? const [];
    return raw
        .map((e) => DocMeta.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  /// Create a blank sheet/doc named [name]. Maps `POST /api/<kind>` with
  /// `{name}` → `{ <itemKey>: row }`.
  Future<DocMeta> create(DocKind kind, String name) async {
    assert(kind != DocKind.pdf, 'PDFs are imported, not created blank');
    final json = await _t.postJson('/api/${kind.api}', {'name': name});
    final row = json[kind.itemKey];
    return DocMeta.fromJson(
      row is Map ? Map<String, dynamic>.from(row) : json,
    );
  }

  /// Import a PDF [file] as a new encrypted document. Maps
  /// `POST /api/pdf/import` (multipart) → `{ file: meta }`.
  Future<DocMeta> importPdf(File file) async {
    final json = await _t.uploadFile('/api/pdf/import', file);
    final row = json['file'];
    return DocMeta.fromJson(
      row is Map ? Map<String, dynamic>.from(row) : json,
    );
  }

  /// Import an `.xlsx` [file] as a new sheet. Maps `POST /api/sheets/import`
  /// (multipart) → `{ sheet: meta }`.
  Future<DocMeta> importSheet(File file) async {
    final json = await _t.uploadFile('/api/sheets/import', file);
    final row = json['sheet'];
    return DocMeta.fromJson(row is Map ? Map<String, dynamic>.from(row) : json);
  }

  /// Import a `.docx` [file] as a new document. Maps `POST /api/docs/import`
  /// (multipart) → `{ doc: meta }`.
  Future<DocMeta> importDoc(File file) async {
    final json = await _t.uploadFile('/api/docs/import', file);
    final row = json['doc'];
    return DocMeta.fromJson(row is Map ? Map<String, dynamic>.from(row) : json);
  }

  /// Fetch a sheet/doc rendered as [format] bytes for export/share. With a
  /// [password] the server returns an AES-256 encrypted .zip (POST, so the
  /// password isn't in the URL); otherwise the plain file (GET).
  Future<List<int>> exportBytes(
    DocKind kind,
    String id,
    String format, {
    String? password,
  }) {
    assert(kind != DocKind.pdf, 'PDFs export via downloadPdf');
    if (password != null && password.isNotEmpty) {
      return _t.postBytes(
        '/api/${kind.api}/$id/export',
        {'format': format, 'password': password},
      );
    }
    return _t.getBytes('/api/${kind.api}/$id/export?format=$format');
  }

  /// Fetch a PDF's bytes for saving/sharing. With a [password] the server
  /// returns an AES-256 encrypted .zip (POST); otherwise the plain PDF (GET).
  Future<List<int>> downloadPdf(String id, {String? password}) {
    if (password != null && password.isNotEmpty) {
      return _t.postBytes('/api/pdf/$id/download', {'password': password});
    }
    return _t.getBytes('/api/pdf/$id/download');
  }

  /// Fetch a sheet/doc with its decrypted Univer snapshot. Maps
  /// `GET /api/<kind>/<id>` → `{id, name, payload, ...}`.
  Future<DocPayload> getPayload(DocKind kind, String id) async {
    assert(kind != DocKind.pdf, 'PDFs are fetched as raw bytes');
    final json = await _t.getJson('/api/${kind.api}/$id');
    return DocPayload.fromJson(json);
  }

  /// Fetch a PDF's raw bytes for the viewer. Maps `GET /api/pdf/<id>/raw`.
  Future<List<int>> getPdfBytes(String id) =>
      _t.getBytes('/api/pdf/$id/raw');

  /// Extract a PDF's text. Maps `GET /api/pdf/<id>/extract` →
  /// `{text, pages}`.
  Future<String> extractPdfText(String id) async {
    final json = await _t.getJson('/api/pdf/$id/extract');
    return json['text']?.toString() ?? '';
  }

  /// Run one ✨ AI edit turn. Maps `POST /api/<kind>/<id>/ai` with
  /// `{instruction}` → `{ok, summary, snapshot|new_pdf_id, error}`.
  Future<AiEditResult> aiEdit(
    DocKind kind,
    String id,
    String instruction,
  ) async {
    final json = await _t.postJson(
      '/api/${kind.api}/$id/ai',
      {'instruction': instruction},
    );
    return AiEditResult.fromJson(json);
  }

  /// Server-side formula recompute for a client-edited [snapshot]. The native
  /// grid has no JS engine, so after a formula edit it posts the snapshot here.
  /// Maps `POST /api/sheets/<id>/recalc` with `{payload}` → `{ok, snapshot}`.
  /// On a malformed response falls back to the snapshot it was given.
  Future<Map<String, dynamic>> recalc(
    String id,
    Map<String, dynamic> snapshot,
  ) async {
    final json = await _t.postJson('/api/sheets/$id/recalc', {'payload': snapshot});
    final snap = json['snapshot'];
    return snap is Map ? Map<String, dynamic>.from(snap) : snapshot;
  }

  /// Persist an edited sheet/doc [payload]. Maps `PUT /api/<kind>/<id>` with
  /// `{name, payload}` (autosave — mirrors the web editor).
  Future<void> save(
    DocKind kind,
    String id,
    String name,
    Map<String, dynamic> payload,
  ) async {
    assert(kind != DocKind.pdf, 'PDFs are immutable; no snapshot save');
    await _t.putJson('/api/${kind.api}/$id', {'name': name, 'payload': payload});
  }

  /// Delete a document. Maps `DELETE /api/<kind>/<id>` →
  /// `{status: "deleted", id}`.
  Future<void> delete(DocKind kind, String id) async {
    await _t.deleteJson('/api/${kind.api}/$id');
  }
}
