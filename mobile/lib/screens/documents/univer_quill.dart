/// Bidirectional converter between Univer `IDocumentData` snapshots and the
/// Quill `Delta` the native Docs editor uses.
///
/// The Univer wire shapes mirror `lazyclaw/docs/snapshot.py` exactly (verified
/// against @univerjs/core 0.24) so a doc round-trips losslessly across the web
/// Univer editor, the backend agent, and this native editor:
///   heading → paragraphStyle.namedStyleType numeric (H1=4, H2=5, H3=6)
///   list    → bullet.listType "BULLET_LIST" / "ORDER_LIST"
///   run     → textRuns[].ts {bl:1, it:1, ul:{s:1}}
///   link    → customRanges with the  /  sentinels in dataStream
library;

import 'package:flutter_quill/quill_delta.dart';

// ── Univer control characters (mirror docs/snapshot.py) ──────────────────────
const String _paraBreak = '\r';
const String _sectionBreak = '\n';
const String _rangeStart = '';
const String _rangeEnd = '';

const Map<int, int> _headingNamedStyle = {1: 4, 2: 5, 3: 6};
const Map<int, int> _namedStyleHeading = {4: 1, 5: 2, 6: 3};
const Map<String, String> _listType = {
  'bullet': 'BULLET_LIST',
  'number': 'ORDER_LIST',
};

// ── Intermediate model (mirrors the Python block dict) ───────────────────────

/// One inline run: text plus optional emphasis and a hyperlink [url].
class DocRun {
  const DocRun(
    this.text, {
    this.bold = false,
    this.italic = false,
    this.underline = false,
    this.url,
  });

  final String text;
  final bool bold;
  final bool italic;
  final bool underline;
  final String? url;
}

/// One paragraph block: a [type] (heading/paragraph/bullet/number), a heading
/// [level] (1–3, else 0), and its inline [runs].
class DocBlock {
  const DocBlock({required this.type, this.level = 0, required this.runs});

  final String type;
  final int level;
  final List<DocRun> runs;

  /// The block's plain text (runs concatenated).
  String get text => runs.map((r) => r.text).join();

  factory DocBlock.paragraph(String text) =>
      DocBlock(type: 'paragraph', runs: [DocRun(text)]);
  factory DocBlock.heading(String text, int level) =>
      DocBlock(type: 'heading', level: level, runs: [DocRun(text)]);
  factory DocBlock.bullet(String text) =>
      DocBlock(type: 'bullet', runs: [DocRun(text)]);
  factory DocBlock.number(String text) =>
      DocBlock(type: 'number', runs: [DocRun(text)]);
}

// ── Univer → blocks ──────────────────────────────────────────────────────────

/// Parse a Univer `IDocumentData` [payload] into typed blocks.
List<DocBlock> blocksFromUniver(Map<String, dynamic>? payload) {
  final body = _asMap(payload?['body']);
  final stream = body['dataStream'];
  if (stream is! String || stream.isEmpty) return const [];

  final startMap = <int, List<Object>>{};
  for (final c in (body['customRanges'] as List? ?? const [])) {
    final m = _asMap(c);
    final s = m['startIndex'];
    final e = m['endIndex'];
    final url = _asMap(m['properties'])['url'];
    if (s is int && e is int && url != null) startMap[s] = [e, url.toString()];
  }

  final styles = _indexStyles(body);
  final parasMeta = (body['paragraphs'] as List? ?? const []);
  final blocks = <DocBlock>[];

  var p = 0;
  for (final (pStart, pEnd) in _paragraphSpans(stream)) {
    final runs = <DocRun>[];
    final buf = StringBuffer();
    Set<String> curFlags = const {};

    void flush() {
      if (buf.isNotEmpty) {
        runs.add(_runWith(buf.toString(), curFlags));
        buf.clear();
      }
      curFlags = const {};
    }

    var i = pStart;
    while (i < pEnd) {
      final ch = stream[i];
      if (ch == _rangeStart && startMap.containsKey(i)) {
        flush();
        final endIdx = startMap[i]![0] as int;
        final url = startMap[i]![1] as String;
        final inner = _strip(stream.substring(i + 1, endIdx));
        final fl = styles[i + 1] ?? const <String>{};
        runs.add(_runWith(inner, fl, url: url));
        i = endIdx + 1;
      } else if (ch == _rangeStart || ch == _rangeEnd) {
        i++;
      } else {
        final fl = styles[i] ?? const <String>{};
        if (!_setEq(fl, curFlags)) {
          flush();
          curFlags = fl;
        }
        buf.write(ch);
        i++;
      }
    }
    flush();

    final meta = p < parasMeta.length
        ? _asMap(parasMeta[p])
        : const <String, dynamic>{};
    final (type, level) = _blockTypeFromMeta(meta);
    blocks.add(DocBlock(
      type: type,
      level: level,
      runs: runs.isEmpty ? const [DocRun('')] : runs,
    ));
    p++;
  }
  return blocks;
}

// ── blocks → Univer ──────────────────────────────────────────────────────────

/// Build a Univer `IDocumentData` body+wrapper from typed [blocks].
Map<String, dynamic> univerFromBlocks(List<DocBlock> blocks) {
  if (blocks.isEmpty) blocks = [DocBlock.paragraph('')];

  final sb = StringBuffer();
  final paras = <Map<String, dynamic>>[];
  final customRanges = <Map<String, dynamic>>[];
  final textRuns = <Map<String, dynamic>>[];
  var cursor = 0;
  var linkCounter = 0;
  var listCounter = 0;
  String? prevListKind;
  String? curListId;

  for (final block in blocks) {
    for (final run in block.runs) {
      final text = _clean(run.text);
      final ts = _ts(run);
      if (run.url != null && run.url!.isNotEmpty) {
        final startToken = cursor;
        sb.write(_rangeStart);
        cursor += 1;
        final textStart = cursor;
        sb.write(text);
        cursor += text.length;
        final endToken = cursor;
        sb.write(_rangeEnd);
        cursor += 1;
        customRanges.add({
          'startIndex': startToken,
          'endIndex': endToken,
          'rangeId': 'link-$linkCounter',
          'rangeType': 0,
          'properties': {'url': run.url},
        });
        linkCounter++;
        if (ts.isNotEmpty && text.isNotEmpty) {
          textRuns.add({'st': textStart, 'ed': textStart + text.length, 'ts': ts});
        }
      } else {
        final runStart = cursor;
        sb.write(text);
        cursor += text.length;
        if (ts.isNotEmpty && text.isNotEmpty) {
          textRuns.add({'st': runStart, 'ed': runStart + text.length, 'ts': ts});
        }
      }
    }

    sb.write(_paraBreak);
    final meta = <String, dynamic>{'startIndex': cursor};
    if (block.type == 'heading') {
      meta['paragraphStyle'] = {
        'namedStyleType': _headingNamedStyle[block.level] ?? 4,
      };
      prevListKind = null;
    } else if (block.type == 'bullet' || block.type == 'number') {
      if (prevListKind != block.type) {
        curListId = 'list-$listCounter';
        listCounter++;
      }
      prevListKind = block.type;
      meta['bullet'] = {
        'listType': _listType[block.type],
        'listId': curListId,
        'nestingLevel': block.level,
      };
    } else {
      prevListKind = null;
    }
    paras.add(meta);
    cursor += 1;
  }

  final dataStream = '$sb$_sectionBreak';
  return {
    'id': 'doc-quill',
    'documentStyle': <String, dynamic>{},
    'body': {
      'dataStream': dataStream,
      'paragraphs': paras,
      'textRuns': textRuns,
      'customRanges': customRanges,
      'sectionBreaks': [
        {'startIndex': dataStream.length - 1},
      ],
    },
  };
}

// ── blocks ⇄ Delta ───────────────────────────────────────────────────────────

/// Build a Quill [Delta] from typed [blocks]. Block formatting (header/list)
/// rides on the line-terminating newline op, per Quill convention.
Delta deltaFromBlocks(List<DocBlock> blocks) {
  final delta = Delta();
  for (final b in blocks) {
    for (final run in b.runs) {
      if (run.text.isEmpty) continue;
      final attrs = <String, dynamic>{};
      if (run.bold) attrs['bold'] = true;
      if (run.italic) attrs['italic'] = true;
      if (run.underline) attrs['underline'] = true;
      if (run.url != null && run.url!.isNotEmpty) attrs['link'] = run.url;
      delta.insert(run.text, attrs.isEmpty ? null : attrs);
    }
    final line = <String, dynamic>{};
    if (b.type == 'heading') {
      line['header'] = b.level;
    } else if (b.type == 'number') {
      line['list'] = 'ordered';
    } else if (b.type == 'bullet') {
      line['list'] = 'bullet';
    }
    delta.insert('\n', line.isEmpty ? null : line);
  }
  return delta;
}

/// Parse a Quill [delta] into typed blocks (inverse of [deltaFromBlocks]).
List<DocBlock> blocksFromDelta(Delta delta) {
  final blocks = <DocBlock>[];
  var runs = <DocRun>[];

  for (final op in delta.toList()) {
    final data = op.data;
    if (data is! String) continue; // embeds (images) unsupported
    final attrs = op.attributes ?? const {};
    final parts = data.split('\n');
    for (var k = 0; k < parts.length; k++) {
      final segment = parts[k];
      if (segment.isNotEmpty) {
        runs.add(DocRun(
          segment,
          bold: attrs['bold'] == true,
          italic: attrs['italic'] == true,
          underline: attrs['underline'] == true,
          url: attrs['link']?.toString(),
        ));
      }
      if (k < parts.length - 1) {
        final (type, level) = _blockTypeFromAttrs(attrs);
        blocks.add(DocBlock(
          type: type,
          level: level,
          runs: runs.isEmpty ? const [DocRun('')] : runs,
        ));
        runs = <DocRun>[];
      }
    }
  }
  if (runs.isNotEmpty) {
    blocks.add(DocBlock(type: 'paragraph', runs: runs));
  }
  return blocks;
}

// ── Convenience (the editor's I/O seam) ──────────────────────────────────────

Delta deltaFromUniver(Map<String, dynamic>? payload) =>
    deltaFromBlocks(blocksFromUniver(payload));

Map<String, dynamic> univerFromDelta(Delta delta) =>
    univerFromBlocks(blocksFromDelta(delta));

// ── helpers ──────────────────────────────────────────────────────────────────

DocRun _runWith(String text, Set<String> flags, {String? url}) => DocRun(
      text,
      bold: flags.contains('bold'),
      italic: flags.contains('italic'),
      underline: flags.contains('underline'),
      url: url,
    );

Map<String, dynamic> _ts(DocRun run) {
  final ts = <String, dynamic>{};
  if (run.bold) ts['bl'] = 1;
  if (run.italic) ts['it'] = 1;
  if (run.underline) ts['ul'] = {'s': 1};
  return ts;
}

(String, int) _blockTypeFromMeta(Map<String, dynamic> meta) {
  final bullet = meta['bullet'];
  if (bullet is Map) {
    final lt = (bullet['listType'] ?? '').toString();
    final kind = lt.startsWith('ORDER') ? 'number' : 'bullet';
    final level = bullet['nestingLevel'];
    return (kind, level is int ? level : 0);
  }
  final ps = meta['paragraphStyle'];
  if (ps is Map) {
    final named = ps['namedStyleType'];
    if (named is int && _namedStyleHeading.containsKey(named)) {
      return ('heading', _namedStyleHeading[named]!);
    }
  }
  return ('paragraph', 0);
}

(String, int) _blockTypeFromAttrs(Map<String, dynamic> attrs) {
  final list = attrs['list'];
  if (list == 'ordered') return ('number', 0);
  if (list == 'bullet') return ('bullet', 0);
  final header = attrs['header'];
  if (header is int && header >= 1 && header <= 3) return ('heading', header);
  return ('paragraph', 0);
}

/// Build an index of char position → emphasis flags from `textRuns`.
Map<int, Set<String>> _indexStyles(Map<String, dynamic> body) {
  final out = <int, Set<String>>{};
  for (final tr in (body['textRuns'] as List? ?? const [])) {
    final m = _asMap(tr);
    final st = m['st'];
    final ed = m['ed'];
    final ts = _asMap(m['ts']);
    if (st is! int || ed is! int) continue;
    final flags = <String>{};
    if (ts['bl'] == 1 || ts['bl'] == true) flags.add('bold');
    if (ts['it'] == 1 || ts['it'] == true) flags.add('italic');
    final ul = ts['ul'];
    if ((ul is Map && (ul['s'] == 1 || ul['s'] == true)) || ul == 1) {
      flags.add('underline');
    }
    if (flags.isEmpty) continue;
    for (var i = st; i < ed; i++) {
      (out[i] ??= <String>{}).addAll(flags);
    }
  }
  return out;
}

/// Absolute (start, end) char spans per paragraph (end = the `\r`).
List<(int, int)> _paragraphSpans(String stream) {
  final spans = <(int, int)>[];
  var prev = 0;
  for (var i = 0; i < stream.length; i++) {
    if (stream[i] == _paraBreak) {
      spans.add((prev, i));
      prev = i + 1;
    }
  }
  return spans;
}

String _clean(String text) => text
    .replaceAll(_paraBreak, ' ')
    .replaceAll(_sectionBreak, ' ')
    .replaceAll(_rangeStart, '')
    .replaceAll(_rangeEnd, '');

String _strip(String s) => s.replaceAll(_rangeStart, '').replaceAll(_rangeEnd, '');

bool _setEq(Set<String> a, Set<String> b) =>
    a.length == b.length && a.every(b.contains);

Map<String, dynamic> _asMap(dynamic v) {
  if (v is Map<String, dynamic>) return v;
  if (v is Map) return v.map((k, val) => MapEntry(k.toString(), val));
  return const {};
}
