/// Spreadsheet formula helper: a catalog of supported functions + a live filter
/// that drives the autocomplete sheet shown while a user types a `=` formula.
///
/// The catalog (assets/sheets/formula_catalog.json) lists the functions the
/// server-side recalc engine (xlcalculator) reliably evaluates, so we never
/// suggest a function the recompute can't handle.
library;

import 'dart:convert';

import 'package:flutter/services.dart' show rootBundle;

/// One catalog entry: a function [name], its [signature], and a one-line [help].
class FormulaFn {
  const FormulaFn(this.name, this.signature, this.help);

  final String name;
  final String signature;
  final String help;

  factory FormulaFn.fromJson(Map<String, dynamic> j) => FormulaFn(
        j['name'].toString(),
        j['signature']?.toString() ?? '',
        j['help']?.toString() ?? '',
      );
}

/// Trailing run of letters at the end of [input] — the function name fragment
/// the user is currently typing (after `=`, `(`, `,`, an operator, or space).
String _currentToken(String input) {
  final m = RegExp(r'[A-Za-z]*$').firstMatch(input);
  return m?.group(0) ?? '';
}

/// Functions to suggest for the current cell [input].
///
/// Returns `[]` unless [input] is a formula (contains `=`). With a partial
/// function token typed, returns the case-insensitive prefix matches; with no
/// token yet (e.g. just `=` or right after `(`), returns the whole catalog.
List<FormulaFn> filterFormulas(List<FormulaFn> all, String input) {
  if (!input.contains('=')) return const [];
  final token = _currentToken(input);
  if (token.isEmpty) return List.unmodifiable(all);
  final up = token.toUpperCase();
  return all.where((f) => f.name.toUpperCase().startsWith(up)).toList();
}

List<FormulaFn>? _cache;

/// Load (and cache) the bundled formula catalog. Returns `[]` if the asset is
/// missing or malformed (the helper just won't suggest anything).
Future<List<FormulaFn>> loadFormulaCatalog() async {
  if (_cache != null) return _cache!;
  try {
    final raw = await rootBundle.loadString('assets/sheets/formula_catalog.json');
    final list = (jsonDecode(raw) as List)
        .map((e) => FormulaFn.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
    _cache = list;
    return list;
  } catch (_) {
    _cache = const [];
    return const [];
  }
}
