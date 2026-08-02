/// [LinkText] — renders text with bare URLs and `[text](url)` named links
/// as tappable spans.
///
/// Pure parsing lives in [tokenizeLinks] (no Flutter dependency), so it's
/// cheaply unit-testable in isolation from the widget.
library;

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:url_launcher/url_launcher.dart';

// ── Parsing ──────────────────────────────────────────────────────────────

/// A single segment of parsed text: either plain text ([url] is null) or a
/// link (rendered as [text] with tap target [url]).
class LinkSpanToken {
  const LinkSpanToken(this.text, this.url);

  final String text;
  final String? url;

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is LinkSpanToken && other.text == text && other.url == url);

  @override
  int get hashCode => Object.hash(text, url);

  @override
  String toString() => 'LinkSpanToken($text, $url)';
}

/// Shared link regexes — mirrors `univer_links.dart`'s conventions.
final _kMdLinkRe = RegExp(r'\[([^\]]+)\]\((https?://[^\s)]+)\)');
final _kBareUrlRe = RegExp(r'https?://[^\s<>"]+');
const _kTrailChars = '.,;:!?)';

/// Tokenize [text] into a list of [LinkSpanToken]s: named `[text](url)`
/// links are matched first, then bare URLs are matched within the
/// remaining plain segments. Trailing `.,;:!?)` characters are trimmed off
/// a bare URL match and left behind as plain text.
///
/// Always returns at least one token (an empty-text plain token for an
/// empty input), so callers never need to special-case the empty list.
List<LinkSpanToken> tokenizeLinks(String text) {
  final tokens = <LinkSpanToken>[];

  var lastEnd = 0;
  for (final match in _kMdLinkRe.allMatches(text)) {
    if (match.start > lastEnd) {
      _tokenizeBareUrls(text.substring(lastEnd, match.start), tokens);
    }
    tokens.add(LinkSpanToken(match.group(1)!, match.group(2)!));
    lastEnd = match.end;
  }
  if (lastEnd < text.length) {
    _tokenizeBareUrls(text.substring(lastEnd), tokens);
  }

  if (tokens.isEmpty) {
    tokens.add(LinkSpanToken(text, null));
  }
  return tokens;
}

/// Scans a plain-text segment (no named links left in it) for bare URLs,
/// trims trailing punctuation off each match, and appends plain/link
/// tokens to [out] in order.
void _tokenizeBareUrls(String segment, List<LinkSpanToken> out) {
  var lastEnd = 0;
  for (final match in _kBareUrlRe.allMatches(segment)) {
    var url = match.group(0)!;
    var end = match.end;
    while (url.isNotEmpty && _kTrailChars.contains(url[url.length - 1])) {
      url = url.substring(0, url.length - 1);
      end--;
    }
    if (url.isEmpty) continue;

    if (match.start > lastEnd) {
      out.add(LinkSpanToken(segment.substring(lastEnd, match.start), null));
    }
    out.add(LinkSpanToken(url, url));
    lastEnd = end;
  }
  if (lastEnd < segment.length) {
    out.add(LinkSpanToken(segment.substring(lastEnd), null));
  }
}

// ── Widget ───────────────────────────────────────────────────────────────

/// Renders [text] with bare URLs and `[text](url)` links as tappable,
/// underlined spans in [AppColors.accent].
///
/// [onOpen] is invoked with the parsed [Uri] when a link is tapped; it
/// defaults to `launchUrl(uri, mode: LaunchMode.externalApplication)`.
/// Injectable so tests never touch the platform URL launcher.
class LinkText extends StatefulWidget {
  const LinkText(this.text, {super.key, this.style, this.onOpen});

  final String text;
  final TextStyle? style;
  final Future<void> Function(Uri uri)? onOpen;

  @override
  State<LinkText> createState() => _LinkTextState();
}

class _LinkTextState extends State<LinkText> {
  final List<TapGestureRecognizer> _recognizers = [];

  @override
  void dispose() {
    for (final r in _recognizers) {
      r.dispose();
    }
    _recognizers.clear();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    for (final r in _recognizers) {
      r.dispose();
    }
    _recognizers.clear();

    final tokens = tokenizeLinks(widget.text);
    final linkStyle = (widget.style ?? const TextStyle()).copyWith(
      color: AppColors.accent,
      decoration: TextDecoration.underline,
      decorationColor: AppColors.accent,
    );

    final children = <InlineSpan>[];
    for (final token in tokens) {
      if (token.url == null) {
        children.add(TextSpan(text: token.text, style: widget.style));
        continue;
      }
      final recognizer = TapGestureRecognizer()
        ..onTap = () => _open(token.url!);
      _recognizers.add(recognizer);
      children.add(TextSpan(
        text: token.text,
        style: linkStyle,
        recognizer: recognizer,
      ));
    }

    return Text.rich(TextSpan(children: children), style: widget.style);
  }

  Future<void> _open(String url) async {
    final uri = Uri.tryParse(url);
    if (uri == null) return;
    try {
      await (widget.onOpen?.call(uri) ??
          launchUrl(uri, mode: LaunchMode.externalApplication));
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.maybeOf(context)?.showSnackBar(
            const SnackBar(content: Text('Could not open link.')));
      }
    }
  }
}
