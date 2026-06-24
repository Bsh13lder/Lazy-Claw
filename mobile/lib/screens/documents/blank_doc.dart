/// Client-side builders for blank Univer `IWorkbookData` / `IDocumentData`
/// snapshots — the mobile mirror of `lazyclaw/sheets/snapshot.py:blank_workbook`
/// and `lazyclaw/docs/snapshot.py:blank_document`.
///
/// Why this exists: creating a sheet/doc on the phone is LOCAL-FIRST (a dirty
/// cache row + an outbox `create`). Before this builder, `createBlank` stored a
/// `null` payload, so opening the brand-new doc parsed an empty workbook (no
/// worksheet, no `sheetOrder`) → a blank, uneditable grid, and the outbox
/// `create` carried no real workbook. Building a valid blank payload on the
/// client makes the new doc immediately editable offline AND gives the server a
/// proper blank workbook when the create eventually pushes.
///
/// The internal Univer ids (`wb-…`, `sh-…`, `doc-…`) are opaque to the rest of
/// the app — they are NOT the document row id (that stays the client UUID minted
/// by `applyLocalCreate`). Only their structure matters, so we generate fresh
/// random ones here.
library;

import '../../local/app_db.dart' show newLocalId;
import 'univer_parse.dart' show kParagraphBreak, kSectionBreak;

/// Univer's defaults for a fresh worksheet. Mirrors `DEFAULT_ROWS` /
/// `DEFAULT_COLS` in `lazyclaw/sheets/snapshot.py`.
const int kDefaultRows = 1000;
const int kDefaultCols = 20;

/// A short random suffix for internal Univer ids. The server uses
/// `uuid4().hex[:12]`; the exact form is irrelevant (the id is opaque), we just
/// keep it short + collision-free by slicing a fresh UUID's hex digits.
String _shortId() => newLocalId().replaceAll('-', '').substring(0, 12);

/// Build a minimal valid empty Univer `IWorkbookData` for a new sheet.
///
/// Mirrors `lazyclaw/sheets/snapshot.py:blank_workbook`: one worksheet
/// (`Sheet1`) with `DEFAULT_ROWS` × `DEFAULT_COLS` and empty `cellData`. The
/// resulting workbook parses (via [parseSheetGrid] / `UniverSheet.fromWorkbook`)
/// into a single editable worksheet — never a blank, sheet-less grid.
Map<String, dynamic> blankWorkbook(String name) {
  final wbId = 'wb-${_shortId()}';
  final shId = 'sh-${_shortId()}';
  final trimmed = name.trim();
  return <String, dynamic>{
    'id': wbId,
    'name': trimmed.isEmpty ? 'Untitled sheet' : trimmed,
    'appVersion': '0.1.0',
    'locale': 'enUS',
    'styles': <String, dynamic>{},
    'sheetOrder': <String>[shId],
    'sheets': <String, dynamic>{
      shId: <String, dynamic>{
        'id': shId,
        'name': 'Sheet1',
        'rowCount': kDefaultRows,
        'columnCount': kDefaultCols,
        'cellData': <String, dynamic>{},
      },
    },
  };
}

/// Build a minimal valid empty Univer `IDocumentData` for a new doc.
///
/// Mirrors `lazyclaw/docs/snapshot.py:blank_document`: a single empty paragraph.
/// `dataStream` is PARAGRAPH_BREAK (`\r`) + SECTION_BREAK (`\n`); the one
/// paragraph's `startIndex` points at the `\r` (index 0); the section break sits
/// at index 1.
Map<String, dynamic> blankDocument(String name) {
  final docId = 'doc-${_shortId()}';
  final trimmed = name.trim();
  return <String, dynamic>{
    'id': docId,
    'name': trimmed.isEmpty ? 'Untitled doc' : trimmed,
    'documentStyle': <String, dynamic>{},
    'body': <String, dynamic>{
      'dataStream': kParagraphBreak + kSectionBreak,
      'paragraphs': <Map<String, dynamic>>[
        <String, dynamic>{'startIndex': 0},
      ],
      'textRuns': <dynamic>[],
      'sectionBreaks': <Map<String, dynamic>>[
        <String, dynamic>{'startIndex': 1},
      ],
    },
  };
}
