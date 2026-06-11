import '../core/api/api_client.dart';
import '../models/note.dart';

// ── Models ──────────────────────────────────────────────────────────────────
//
// Shapes verified against the backend (lazyclaw/gateway/routes/lazybrain.py +
// lazyclaw/lazybrain/store.py / embeddings.py / ask.py) and the web client
// (web/src/api.ts LazyBrainNote / LazyBrainTag / SemanticSearchResponse /
// AskResponse). Note-shaped payloads reuse the existing [Note] model.

/// One aggregated tag count as returned by `GET /api/lazybrain/tags`:
/// `{ "tags": [{ "tag": "journal", "count": 14 }, …] }`.
class BrainTag {
  final String tag;
  final int count;

  const BrainTag({required this.tag, required this.count});

  factory BrainTag.fromJson(Map<String, dynamic> json) => BrainTag(
        tag: json['tag']?.toString() ?? '',
        count: _toInt(json['count']) ?? 0,
      );

  @override
  String toString() => 'BrainTag($tag: $count)';
}

/// One semantic-search hit: a full note dict plus an optional `_score`
/// (cosine similarity, absent on bm25/substring fallback paths).
class SemanticHit {
  final Note note;
  final double? score;

  const SemanticHit({required this.note, this.score});

  factory SemanticHit.fromJson(Map<String, dynamic> json) => SemanticHit(
        note: Note.fromJson(json),
        score: _toDouble(json['_score']),
      );
}

/// Response of `POST /api/lazybrain/semantic-search`:
/// `{ query, results: [note + _score?], source }` where `source` is one of
/// `hybrid | semantic | bm25 | substring | empty`.
class SemanticSearchResult {
  final String query;
  final List<SemanticHit> hits;
  final String source;

  const SemanticSearchResult({
    required this.query,
    required this.hits,
    required this.source,
  });

  factory SemanticSearchResult.fromJson(Map<String, dynamic> json) {
    final raw = json['results'] as List? ?? const [];
    return SemanticSearchResult(
      query: json['query']?.toString() ?? '',
      hits: raw
          .map((e) => SemanticHit.fromJson(Map<String, dynamic>.from(e as Map)))
          .toList(),
      source: json['source']?.toString() ?? 'empty',
    );
  }
}

/// Response of `POST /api/lazybrain/ask` (ask.ask_notes):
/// `{ question, answer, sources: [title, …], source_count,
///    retrieval_source? }`.
class AskResult {
  final String question;
  final String answer;
  final List<String> sources;
  final int sourceCount;
  final String? retrievalSource;

  const AskResult({
    required this.question,
    required this.answer,
    required this.sources,
    required this.sourceCount,
    this.retrievalSource,
  });

  factory AskResult.fromJson(Map<String, dynamic> json) {
    final raw = json['sources'] as List? ?? const [];
    return AskResult(
      question: json['question']?.toString() ?? '',
      answer: json['answer']?.toString() ?? '',
      sources: raw.map((e) => e.toString()).toList(),
      sourceCount: _toInt(json['source_count']) ?? 0,
      retrievalSource: json['retrieval_source']?.toString(),
    );
  }
}

// ── Transport seam ──────────────────────────────────────────────────────────

/// Minimal transport interface — only the verbs the LazyBrain read surface
/// uses (GET for lists, POST for semantic-search/ask). Faked in tests.
abstract class LazyBrainTransport {
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  });

  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  );
}

class DioLazyBrainTransport implements LazyBrainTransport {
  final ApiClient _client;
  DioLazyBrainTransport(this._client);

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) =>
      _client.get<Map<String, dynamic>>(
        path,
        queryParams: queryParams,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );

  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) =>
      _client.post<Map<String, dynamic>>(
        path,
        data: body,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );
}

// ── Repository ──────────────────────────────────────────────────────────────

/// LazyBrain (PKM) read surface — journal timeline, tag counts, pinned notes,
/// tag-filtered notes, semantic search, and RAG ask.
///
/// Endpoints (mirrored from web/src/api.ts):
///   GET  /api/lazybrain/journal?limit=14      → { notes: [Note] }
///   GET  /api/lazybrain/tags                  → { tags: [{tag, count}] }
///   GET  /api/lazybrain/notes?pinned=true     → { notes: [Note] }
///   GET  /api/lazybrain/notes?tag={tag}       → { notes: [Note] }
///   POST /api/lazybrain/semantic-search       → { query, results, source }
///   POST /api/lazybrain/ask                   → { question, answer, sources,
///                                                 source_count }
class LazyBrainRepository {
  final LazyBrainTransport _t;
  LazyBrainRepository(this._t);

  /// Recent journal pages, newest first (server default: 14 days).
  Future<List<Note>> fetchJournal({int limit = 14}) async {
    final json = await _t.getJson(
      '/api/lazybrain/journal',
      queryParams: {'limit': limit},
    );
    return _parseNotes(json);
  }

  /// Aggregated tag counts, most-used first.
  Future<List<BrainTag>> fetchTags() async {
    final json = await _t.getJson('/api/lazybrain/tags');
    final raw = json['tags'] as List? ?? const [];
    return raw
        .map((e) => BrainTag.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  /// Pinned notes only.
  Future<List<Note>> fetchPinned() async {
    final json = await _t.getJson(
      '/api/lazybrain/notes',
      queryParams: {'pinned': 'true'},
    );
    return _parseNotes(json);
  }

  /// Notes carrying [tag]. Throws [ArgumentError] on a blank tag — the
  /// backend would silently return the unfiltered list otherwise.
  Future<List<Note>> fetchNotesByTag(String tag) async {
    final trimmed = tag.trim();
    if (trimmed.isEmpty) {
      throw ArgumentError.value(tag, 'tag', 'must not be blank');
    }
    final json = await _t.getJson(
      '/api/lazybrain/notes',
      queryParams: {'tag': trimmed},
    );
    return _parseNotes(json);
  }

  /// Hybrid semantic search over the user's notes (top-[k]).
  /// Throws [ArgumentError] on a blank query (server enforces min_length=1).
  Future<SemanticSearchResult> semanticSearch(String query, {int k = 10}) async {
    final trimmed = query.trim();
    if (trimmed.isEmpty) {
      throw ArgumentError.value(query, 'query', 'must not be blank');
    }
    final json = await _t.postJson(
      '/api/lazybrain/semantic-search',
      {'query': trimmed, 'k': k},
    );
    return SemanticSearchResult.fromJson(json);
  }

  /// RAG question-answering grounded in the user's notes (top-[k] retrieval).
  /// Throws [ArgumentError] on a blank question (server enforces min_length=1).
  Future<AskResult> ask(String question, {int k = 8}) async {
    final trimmed = question.trim();
    if (trimmed.isEmpty) {
      throw ArgumentError.value(question, 'question', 'must not be blank');
    }
    final json = await _t.postJson(
      '/api/lazybrain/ask',
      {'question': trimmed, 'k': k},
    );
    return AskResult.fromJson(json);
  }

  List<Note> _parseNotes(Map<String, dynamic> json) {
    final raw = json['notes'] as List? ?? const [];
    return raw
        .map((e) => Note.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }
}

// ── Private parse helpers ───────────────────────────────────────────────────

int? _toInt(dynamic v) {
  if (v == null) return null;
  if (v is int) return v;
  if (v is double) return v.toInt();
  return int.tryParse(v.toString());
}

double? _toDouble(dynamic v) {
  if (v == null) return null;
  if (v is double) return v;
  if (v is int) return v.toDouble();
  return double.tryParse(v.toString());
}
