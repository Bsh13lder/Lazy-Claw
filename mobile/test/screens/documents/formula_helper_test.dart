import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/documents/formula_helper.dart';

void main() {
  final fns = [
    const FormulaFn('SUM', 'SUM(range)', 'Adds numbers'),
    const FormulaFn('AVERAGE', 'AVERAGE(range)', 'Mean of numbers'),
    const FormulaFn('IF', 'IF(cond, a, b)', 'Branch on a condition'),
    const FormulaFn('ABS', 'ABS(number)', 'Absolute value'),
  ];

  test('filters by case-insensitive prefix after =', () {
    expect(filterFormulas(fns, '=su').map((f) => f.name).toList(), ['SUM']);
    expect(filterFormulas(fns, '=A').map((f) => f.name).toList(), ['AVERAGE', 'ABS']);
  });

  test('helps inside a function argument list', () {
    expect(filterFormulas(fns, '=SUM(a1,av').last.name, 'AVERAGE');
  });

  test('no leading = → no helper', () {
    expect(filterFormulas(fns, 'plain text'), isEmpty);
    expect(filterFormulas(fns, '42'), isEmpty);
  });

  test('bare = (no token yet) → all functions', () {
    expect(filterFormulas(fns, '=').length, fns.length);
  });

  test('no match → empty list (not all)', () {
    expect(filterFormulas(fns, '=zzz'), isEmpty);
  });

  test('FormulaFn.fromJson parses catalog rows', () {
    final f = FormulaFn.fromJson({
      'name': 'VLOOKUP',
      'signature': 'VLOOKUP(key, range, index)',
      'help': 'Look up a value',
    });
    expect(f.name, 'VLOOKUP');
    expect(f.signature, contains('range'));
  });
}
