import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_quill/quill_delta.dart';
import 'package:lazyclaw_mobile/screens/documents/univer_quill.dart';

void main() {
  group('blocks ⇄ Univer', () {
    test('plain paragraph round-trips', () {
      final uni = univerFromBlocks([DocBlock.paragraph('hello')]);
      final back = blocksFromUniver(uni);
      expect(back.first.type, 'paragraph');
      expect(back.first.text, 'hello');
    });

    test('heading level preserved', () {
      final uni = univerFromBlocks([DocBlock.heading('Title', 2)]);
      expect(uni['body']['paragraphs'][0]['paragraphStyle']['namedStyleType'], 5);
      final back = blocksFromUniver(uni);
      expect(back.first.type, 'heading');
      expect(back.first.level, 2);
    });

    test('numbered list emits ORDER_LIST and round-trips', () {
      final uni = univerFromBlocks([DocBlock.number('a'), DocBlock.number('b')]);
      expect(uni['body']['paragraphs'][0]['bullet']['listType'], 'ORDER_LIST');
      final back = blocksFromUniver(uni);
      expect(back.map((b) => b.type).toList(), ['number', 'number']);
    });

    test('bold + link survive Univer round-trip', () {
      final uni = univerFromBlocks([
        DocBlock(type: 'paragraph', runs: [
          const DocRun('see '),
          const DocRun('here', url: 'https://x.io', bold: true),
        ]),
      ]);
      final back = blocksFromUniver(uni);
      final link = back.first.runs.firstWhere((r) => r.url != null);
      expect(link.text, 'here');
      expect(link.url, 'https://x.io');
      expect(link.bold, isTrue);
    });
  });

  group('blocks ⇄ Delta', () {
    test('numbered list maps to list:ordered on the newline', () {
      final delta = deltaFromBlocks([DocBlock.number('a'), DocBlock.number('b')]);
      final ordered = delta
          .toList()
          .where((op) => op.attributes?['list'] == 'ordered')
          .length;
      expect(ordered, 2);
      final back = blocksFromDelta(delta);
      expect(back.map((b) => b.type).toList(), ['number', 'number']);
    });

    test('heading maps to header attribute', () {
      final delta = deltaFromBlocks([DocBlock.heading('T', 1)]);
      expect(delta.toList().any((op) => op.attributes?['header'] == 1), isTrue);
      expect(blocksFromDelta(delta).first.type, 'heading');
    });

    test('bold + link inline attributes round-trip via Delta', () {
      final blocks = [
        DocBlock(type: 'paragraph', runs: [
          const DocRun('go '),
          const DocRun('site', url: 'https://x.io', bold: true),
        ]),
      ];
      final back = blocksFromDelta(deltaFromBlocks(blocks));
      final link = back.first.runs.firstWhere((r) => r.url != null);
      expect(link.url, 'https://x.io');
      expect(link.bold, isTrue);
    });
  });

  group('full Univer ⇄ Delta', () {
    test('heading + numbered list + bold link survive the whole loop', () {
      final uni = univerFromBlocks([
        DocBlock.heading('Setup', 1),
        DocBlock.number('Install'),
        DocBlock(type: 'paragraph', runs: [
          const DocRun('see '),
          const DocRun('docs', url: 'https://x.io', bold: true),
        ]),
      ]);
      final delta = deltaFromUniver(uni);
      final back = univerFromDelta(delta);
      final blocks = blocksFromUniver(back);
      expect(blocks[0].type, 'heading');
      expect(blocks[0].level, 1);
      expect(blocks[1].type, 'number');
      final link = blocks[2].runs.firstWhere((r) => r.url != null);
      expect(link.url, 'https://x.io');
      expect(link.bold, isTrue);
    });

    test('empty doc yields a single empty paragraph delta', () {
      final delta = deltaFromUniver({'body': {'dataStream': '\r\n', 'paragraphs': [{'startIndex': 0}]}});
      // One newline op for the single empty paragraph.
      expect(delta.toList().length, 1);
      expect(delta.toList().first.data, '\n');
    });
  });
}
