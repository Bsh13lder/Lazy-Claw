/// Pins the voice/chat generation-parameter split.
///
/// The voice turn is aggressively capped so a spoken answer can't run for
/// minutes. The local CHAT tab shares the same engine, so the risk this suite
/// exists to catch is that tuning voice collaterally shortens chat replies.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local_ai/local_llm_engine.dart';

void main() {
  group('LocalGenOptions.voice', () {
    test('caps a spoken reply to roughly one or two sentences', () {
      const v = LocalGenOptions.voice;
      expect(v.maxTokens, 128);
      // At the ~6-15 tok/s this device manages on a 4B Q4, 1024 tokens is a
      // 68-170s generation. 128 keeps the worst case near 13s.
      expect(v.maxTokens, lessThan(const LocalGenOptions().maxTokens));
    });

    test('is low-variance', () {
      expect(LocalGenOptions.voice.temperature, 0.3);
      expect(LocalGenOptions.voice.minP, 0.05);
      expect(LocalGenOptions.voice.repeatPenalty, 1.05);
    });

    test('disables thinking', () {
      // Gemma 4's template injects a <|think|> turn when this is set, and the
      // binding defaults it to TRUE — a spoken reply would open with reasoning.
      expect(LocalGenOptions.voice.enableThinking, isFalse);
      expect(const LocalGenOptions().enableThinking, isTrue,
          reason: 'the default must stay as the binding has it');
    });

    test('stops lists at the source, not with a cleanup regex', () {
      // A regex can strip the bullet marker but not the list structure, so the
      // model must never start one.
      expect(LocalGenOptions.voice.stopSequences, contains('\n- '));
      expect(LocalGenOptions.voice.stopSequences, contains('\n1. '));
      expect(LocalGenOptions.voice.stopSequences, contains('\n\n'));
    });

    test('streams in finer batches so the first sentence lands sooner', () {
      expect(LocalGenOptions.voice.streamBatchTokens,
          lessThan(const LocalGenOptions().streamBatchTokens));
    });
  });

  group('defaults (the local chat tab)', () {
    test('are unchanged — long-form replies survive the voice tuning', () {
      const d = LocalGenOptions();
      expect(d.maxTokens, 1024);
      expect(d.temperature, 0.7);
      expect(d.topP, 0.9);
      expect(d.topK, 40);
      expect(d.minP, 0.0);
      expect(d.repeatPenalty, 1.1);
      expect(d.stopSequences, isEmpty);
      expect(d.streamBatchTokens, 8);
    });

    test('LocalGenOptions.chat is exactly the implicit default', () {
      const a = LocalGenOptions.chat;
      const b = LocalGenOptions();
      expect(a.maxTokens, b.maxTokens);
      expect(a.temperature, b.temperature);
      expect(a.enableThinking, b.enableThinking);
      expect(a.stopSequences, b.stopSequences);
      expect(a.streamBatchTokens, b.streamBatchTokens);
    });
  });
}
