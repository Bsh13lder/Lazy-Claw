import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/documents/sheet_selection.dart';

void main() {
  group('SheetSelection', () {
    test('single constructs anchor == focus', () {
      const s = SheetSelection(2, 3, 2, 3);
      expect(s.isSingle, isTrue);
      expect(s.anchorRow, 2);
      expect(s.anchorCol, 3);
    });

    test('factory single matches direct constructor', () {
      final a = SheetSelection.single(5, 7);
      const b = SheetSelection(5, 7, 5, 7);
      expect(a, equals(b));
    });

    test('range is normalized when anchor is bottom-right of focus', () {
      final s = SheetSelection.single(4, 5).extendTo(1, 2);
      final r = s.range;
      expect(r.r1, 1);
      expect(r.c1, 2);
      expect(r.r2, 4);
      expect(r.c2, 5);
    });

    test('range is normalized when anchor is top-left of focus', () {
      final s = SheetSelection(0, 0, 3, 4);
      final r = s.range;
      expect(r.r1, 0);
      expect(r.c1, 0);
      expect(r.r2, 3);
      expect(r.c2, 4);
    });

    test('extendTo keeps anchor fixed', () {
      final s = SheetSelection.single(2, 2).extendTo(5, 6);
      expect(s.anchorRow, 2);
      expect(s.anchorCol, 2);
      expect(s.focusRow, 5);
      expect(s.focusCol, 6);
      expect(s.isSingle, isFalse);
    });

    test('extendTo back to anchor makes it single', () {
      final s = SheetSelection.single(3, 3).extendTo(7, 7).extendTo(3, 3);
      expect(s.isSingle, isTrue);
    });

    test('equality and hashCode', () {
      final a = SheetSelection.single(1, 2);
      final b = SheetSelection.single(1, 2);
      final c = SheetSelection.single(1, 3);
      expect(a, equals(b));
      expect(a.hashCode, b.hashCode);
      expect(a, isNot(equals(c)));
    });
  });

  group('cellFromOffset', () {
    // 44px gutter, 36px header, 100px cols, 36px rows.
    const gutterW = 44.0;
    const headerH = 36.0;
    const colW = 100.0;
    const rowH = 36.0;

    test('tap in top-left gutter corner → (-1, -1)', () {
      final r = cellFromOffset(
        const Offset(10, 10),
        gutterW: gutterW,
        headerH: headerH,
        colW: colW,
        rowH: rowH,
      );
      expect(r, (-1, -1));
    });

    test('tap in column header (row = -1)', () {
      // x=100 → in col 0 header (44..144)
      final r = cellFromOffset(
        const Offset(100, 10),
        gutterW: gutterW,
        headerH: headerH,
        colW: colW,
        rowH: rowH,
      );
      expect(r.$1, -1);
      expect(r.$2, 0);
    });

    test('tap in second column header', () {
      // x = 44 + 1.5*100 = 194 → col 1
      final r = cellFromOffset(
        const Offset(194, 10),
        gutterW: gutterW,
        headerH: headerH,
        colW: colW,
        rowH: rowH,
      );
      expect(r.$1, -1);
      expect(r.$2, 1);
    });

    test('tap in row gutter → (row, -1)', () {
      // y = 36 + 0.5*36 = 54 → row 0
      final r = cellFromOffset(
        const Offset(20, 54),
        gutterW: gutterW,
        headerH: headerH,
        colW: colW,
        rowH: rowH,
      );
      expect(r.$1, 0);
      expect(r.$2, -1);
    });

    test('tap in second row gutter', () {
      // y = 36 + 1.5*36 = 90 → row 1
      final r = cellFromOffset(
        const Offset(10, 90),
        gutterW: gutterW,
        headerH: headerH,
        colW: colW,
        rowH: rowH,
      );
      expect(r.$1, 1);
      expect(r.$2, -1);
    });

    test('tap on cell (0, 0)', () {
      final r = cellFromOffset(
        const Offset(94, 54), // just inside gutter+col0, header+row0
        gutterW: gutterW,
        headerH: headerH,
        colW: colW,
        rowH: rowH,
      );
      expect(r, (0, 0));
    });

    test('tap on cell (1, 2)', () {
      // x = 44 + 2.5*100 = 294; y = 36 + 1.5*36 = 90
      final r = cellFromOffset(
        const Offset(294, 90),
        gutterW: gutterW,
        headerH: headerH,
        colW: colW,
        rowH: rowH,
      );
      expect(r, (1, 2));
    });

    test('negative x (before gutter) → col clamped to 0 in gutter zone', () {
      // negative x but y > headerH = row gutter zone
      final r = cellFromOffset(
        const Offset(-5, 54),
        gutterW: gutterW,
        headerH: headerH,
        colW: colW,
        rowH: rowH,
      );
      expect(r.$2, -1); // gutter zone
    });

    test('negative y (above header) in column zone → row -1, col 0', () {
      final r = cellFromOffset(
        const Offset(94, -5),
        gutterW: gutterW,
        headerH: headerH,
        colW: colW,
        rowH: rowH,
      );
      expect(r.$1, -1);
    });

    test('exact boundary at gutter edge maps to col 0', () {
      final r = cellFromOffset(
        const Offset(44, 54),
        gutterW: gutterW,
        headerH: headerH,
        colW: colW,
        rowH: rowH,
      );
      expect(r, (0, 0));
    });

    test('exact boundary at header edge maps to row 0', () {
      final r = cellFromOffset(
        const Offset(94, 36),
        gutterW: gutterW,
        headerH: headerH,
        colW: colW,
        rowH: rowH,
      );
      expect(r, (0, 0));
    });
  });
}
