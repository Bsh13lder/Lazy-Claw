/// Shared money/amount formatting helpers for the Expenses screen.
library;

import '../../core/due_date.dart';

/// Format a currency amount with the appropriate symbol.
/// Omits decimals when the value is a whole number.
String fmtMoney(String currency, double v) {
  final sym = _sym(currency);
  if (v == v.truncateToDouble()) return '$sym${v.toInt()}';
  return '$sym${v.toStringAsFixed(2)}';
}

String _sym(String currency) {
  switch (currency) {
    case 'USD':
      return '\$';
    case 'EUR':
      return '€';
    case 'GBP':
      return '£';
    case 'JPY':
      return '¥';
    default:
      return '$currency ';
  }
}

/// Groups a flat list of items by a key derived from each item,
/// returning an ordered map (insertion order = order of first occurrence).
Map<K, List<T>> groupBy<T, K>(Iterable<T> items, K Function(T) key) {
  final result = <K, List<T>>{};
  for (final item in items) {
    result.putIfAbsent(key(item), () => []).add(item);
  }
  return result;
}

/// Returns the date portion of an ISO-8601 string (first 10 chars),
/// or 'Unknown date' if the string is null/short.
String dateLabel(String? isoString) {
  if (isoString == null || isoString.length < 10) return 'Unknown date';
  return isoString.substring(0, 10);
}

/// Human-readable date label for an ISO date string like "2026-06-04".
/// Returns "Today", "Yesterday", or "Jun 4" style.
String friendlyDate(String? isoString) {
  if (isoString == null || isoString.length < 10) return 'Unknown date';
  try {
    final d = DateTime.parse(isoString.substring(0, 10));
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final diff = today.difference(DateTime(d.year, d.month, d.day)).inDays;
    if (diff == 0) return 'Today';
    if (diff == 1) return 'Yesterday';
    final months = [
      '',
      'Jan',
      'Feb',
      'Mar',
      'Apr',
      'May',
      'Jun',
      'Jul',
      'Aug',
      'Sep',
      'Oct',
      'Nov',
      'Dec',
    ];
    final sameYear = d.year == now.year;
    return sameYear
        ? '${months[d.month]} ${d.day}'
        : '${months[d.month]} ${d.day}, ${d.year}';
  } catch (_) {
    return isoString.substring(0, 10);
  }
}

/// A compact "saved at" label for an expense's `created_at` timestamp, e.g.
/// `Today · 2:32 PM`, `Yesterday · 9:05 AM`, or `Jan 15, 2024 · 2:32 PM`.
///
/// [iso] is an ISO-8601 timestamp (usually UTC, with a trailing `Z`). It is
/// converted to the device's local time so the date + clock read naturally.
/// Returns null for null/empty/unparseable input so callers can render nothing
/// rather than crash. Pure (no Flutter) so it's trivially unit-testable.
String? formatSavedAt(String? iso) {
  if (iso == null || iso.isEmpty) return null;
  final parsed = DateTime.tryParse(iso);
  if (parsed == null) return null;
  final local = parsed.toLocal();
  final date = friendlyDate(local.toIso8601String());
  final time = formatClock12(local.hour, local.minute);
  return '$date · $time';
}
