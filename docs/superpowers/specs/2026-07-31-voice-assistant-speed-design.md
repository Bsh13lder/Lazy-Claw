# "Hey Lazy" voice assistant — speed & reliability overhaul

**Date:** 2026-07-31
**Branch:** `feat/voice-assistant-speed`
**Target device:** Xiaomi Mi 15 — `24129PN74G`, SM8750 (Snapdragon 8 Elite, Oryon V2), Adreno 830,
10.9 GB physical RAM + ~12 GB ZRAM, arm64-v8a only, Android 16 / SDK 36, HyperOS V816.

## Problem

The on-device voice assistant is "too slow and doesn't work properly" versus Google Assistant.
Investigation found this is **four independent failures stacked**, not one slow model:

1. The feature was never able to run on the device at all (no mic permission, no LLM model).
2. A listener-ownership bug wedges the UI at "Listening…" on the most common STT outcomes.
3. Time-to-first-audio is the *entire* generation time, because TTS waits for the stream to close.
4. Inference is pinned to CPU with a 1024-token cap.

Fixing the model would have addressed none of them.

---

## Root causes

### Device state (all verified over ADB, 2026-07-31)

| Finding | Evidence | Status |
|---|---|---|
| `RECORD_AUDIO` denied; appop `ignore` (silent denial) | `dumpsys package` user 0 | **fixed** via `pm grant` + `appops set` |
| Not battery-optimisation exempt | `dumpsys deviceidle whitelist` empty | **fixed** via `deviceidle whitelist +` |
| No GGUF model on device — `…/files` dir never created | `ls /sdcard/Android/data/<pkg>/files` → ENOENT | open (Phase −1) |
| Assist app is DuckDuckGo, not Lazy | `settings get secure assistant` | open (Phase −1) |
| Wake FGS not running | `dumpsys activity services` empty | consequence of the mic denial |

ADB grants can be revoked by HyperOS auto-manage on reboot — the permission must also be set
to **Allow all the time** through Settings.

`READ_EXTERNAL_STORAGE` is denied but is **not** a blocker: `local_model_store.dart:3-8`
deliberately uses the app-specific external dir, which needs no runtime permission.

### Code defects

| # | Sev | Defect | Location |
|---|---|---|---|
| A | CRITICAL | `SpeechToText()` is a process singleton; `initialize` early-returns without replacing listeners. The wake service initialises it first with empty handlers, so the assistant's `onError` is never wired. `error_no_match` / `error_speech_timeout` / `error_busy` fire into a no-op → phase stuck at `listening` forever, mic dead, tap does nothing. | `native_wake_service.dart:22-25`, `main.dart:185-187`, `speech_to_text.dart:314` |
| B | CRITICAL | A no-speech turn never delivers a final result → same wedge. | `lazy_assistant_controller.dart:141-145` |
| C | CRITICAL | TTS starts only after the generation stream closes. | `lazy_assistant_controller.dart:255, 340-361` |
| D | CRITICAL | `maxTokens: 1024`, no stop sequences → 68–170 s generations at 6–15 tok/s. | `llamadart_engine.dart:29, 85` |
| E | HIGH | flutter_tts **silently discards** an overlapping `speak()`; and never resolves the pending future on engine error → permanent hang. Reachable today: `setLanguage` is never called, so a Spanish/Georgian reply with no installed voice wedges the assistant. | `FlutterTtsPlugin.kt:304-309, 169-199`; `lazy_assistant_controller.dart:378-384` |
| F | HIGH | Endpoint tail = 1600 ms silence window **+** hidden 2000 ms `finalTimeout` = 3.6 s worst case (Google ≈ 0.5–0.8 s). | `lazy_assistant_controller.dart:71`, `speech_to_text.dart:123,351` |
| G | HIGH | WebSocket torn down and re-dialled **every** cloud turn: 150–800 ms, can drop in-flight frames. | `cloud_turn_client.dart:94-103` → `chat_socket.dart:106-111` |
| H | HIGH | `_stt.listen` throw is uncaught (called unawaited) → stuck phase + unhandled async error. | `lazy_assistant_controller.dart:127-136` |
| I | MED | `[[NEEDS_CLOUD]]` uses `.contains`, is unreachable today (prompt never mentions it), and becomes a double-answer hazard under streaming TTS. | `lazy_assistant_controller.dart:236-247` |
| J | MED | Barge-in does not cancel local generation; abandoned stream keeps overwriting `state.response`. | `:156-159`, `local_llm_engine.dart:41-63` |
| K | MED | Second wake pushes a **duplicate** assistant screen onto the stack. | `main.dart:291-293`, `MainActivity.kt:162-168` |
| L | MED | `MIC_HANDOFF_MS = 45000` fixed: deaf for 40 s after a fast turn, or Vosk hears the TTS on a slow one and self-triggers on "hey lazy" in a reply. | `WakeWordService.kt:55, 249-260` |
| M | MED | `_triedAutoLoad` latches on *attempt*, so a failed model load strands the screen permanently. | `lazy_assistant_screen.dart:30, 43-54` |
| N | MED | Consent state leaks on screen pop → wedged `awaitingCloudConsent`. | `:75, 404` + screen `:89-95` |

The existing test fakes yield the whole reply in one chunk and never throw, so they are
structurally incapable of catching C, E, I or J — the "fake transports must throw the production
exception shape" lesson from the sync engine, repeated.

---

## Design

### Phase −1 — unblock the device (no code)

Grant mic (done), battery whitelist (done), set Lazy as the assist app, download a model.
**Nothing downstream is measurable until this is complete** — the pipeline has never executed
end-to-end on this phone.

### Phase 0 — generation params and streaming TTS

The perceived fix. No new dependencies.

**Params.** Introduce an engine-agnostic `LocalGenOptions` with a `voice` preset, passed per-call
so the local chat tab keeps long replies:

| Param | Today | Voice |
|---|---|---|
| `maxTokens` | 1024 | **128** |
| `temp` | 0.7 | **0.3** |
| `minP` | 0.0 | 0.05 |
| `repeatPenalty` | 1.1 | 1.05 |
| `stopSequences` | `[]` | `['\n\n', '\nUser:', '\nAssistant:', '\n- ', '\n1. ']` |
| `enableThinking` | **true** (llamadart default) | **false** |
| `streamBatchTokenThreshold` | 8 | 4 |

`contextSize` stays 4096 — it is a *model-load* parameter, so making it per-call would force a
reload per turn.

`enableThinking: false` is **mandatory, not hygiene**: Gemma 4's canonical chat template injects a
`<|think|>` block whenever `enable_thinking` is true (verified in the GGUF's embedded template),
and llamadart defaults it to true. Left alone, every Gemma 4 voice reply would begin with reasoning
tokens.

**System prompt** gets a numeric ceiling stated first ("ONE or TWO short sentences. Never more."),
the read-aloud frame, and the escalation contract last. Length is then enforced in three
independent layers: prompt, `maxTokens`, stop sequences.

**Streaming TTS.** Three new pure units:

- `sentence_streamer.dart` — chunk-boundary-safe segmentation. Punctuation-driven only, with one
  character of mandatory lookahead (which alone defeats the decimal case), an abbreviation/initial/
  ordinal guard, ellipsis collapsing, a `minChars` floor to avoid choppy one-word utterances, and a
  lower `firstMinChars` for the first utterance to buy the "audio starts immediately" feel.
  **No capitalisation heuristics** — they break Georgian, which has no case.
- `SpeechQueue` — epoch-based serialised queue. Serialisation is *forced*: flutter_tts silently
  discards an overlapping `speak()`, and `setQueueMode(QUEUE_ADD)` is not an escape because
  `awaitSpeakCompletion` only works under `QUEUE_FLUSH`. Every await is watchdogged, because the
  plugin's error path never resolves the future; the timeout calls `stop()`, which does release it.
- `Speaker` interface — makes TTS injectable for tests.

`_clean()` is **not** made incremental. It runs per *emitted sentence*, which the segmenter
guarantees is a complete whitespace-delimited unit, so a markdown token can never be split across
the boundary. This also fixes the cosmetic bug where raw asterisks flash on screen during streaming.

**Phase model is unchanged** — `thinking` now means "generating, nothing spoken yet" and `speaking`
means "first utterance started", so the screen diff is 3 lines.

**The `[[NEEDS_CLOUD]]` hazard is designed out, not worked around.** Speech starts *disarmed* on the
local branch; sentences are held until the sentinel prefix is ruled out (a 15-char prefix test, so
arming happens within a chunk or two). Escalation is gated on `!armed`. The invariant: *escalation
is only reachable on a turn where nothing was ever spoken.* The prompt contract becomes prefix-only.

### Phase 1 — OpenCL GPU offload

`llamadart_native_backends: [opencl]`, `preferredBackend: GpuBackend.opencl`, **`gpuLayers: 99`**.

Verified against the actual arm64 binaries: `libggml-opencl.so` exports only `ggml_backend_init`,
its `.init_array` is 3 trivial static constructors, and llamadart's startup path loads CPU only
(hardened in 0.6.4). So the `.so` is provably inert while `preferredBackend` stays `cpu` — the
Vulkan black-screen class does not recur, and OpenCL links `libOpenCL.so`, not `libvulkan.so`.
Device gate passes: `/vendor/lib64/libOpenCL.so` exists **and** is listed in
`/vendor/etc/public.libraries.txt`.

Cost **2.1 MB** (vs 48.4 MB for Vulkan). Two traps: `gpuLayers: 0` plus a GPU backend pays driver +
JIT init and then runs on CPU anyway; and `preferredBackend: auto` silently resolves to `cpu` on
Android. Gate on `listGpuDevices(probeBackends: [GpuBackend.opencl])` returning non-empty, behind a
user-visible toggle, with CPU fallback.

Bump llamadart 0.8.5 → 0.8.17 (no breaking changes; 0.8.13 hardened the external draft-model path).

### Phase 2 — sherpa-onnx speech stack

Replaces Vosk **and** `speech_to_text`. This is forced, not preferred: Android's `SpeechRecognizer`
owns the mic exclusively and cannot accept buffered PCM, so the lost-first-second bug is unfixable
while it is in the loop.

**Architecture.** Kotlin FGS keeps owning one `AudioRecord` that is never torn down (retaining
`START_NOT_STICKY`, wake-lock, HyperOS deep-links, FSI); Dart owns all recognition in one dedicated
isolate. Recognition must be in Dart because the sherpa pub package ships no JNI library and has no
Maven artifact — running it from Kotlin would vendor a second 20 MB onnxruntime.

On a wake hit the ASR is fed a 1.2 s pre-roll **including the wake phrase** from a Dart ring buffer;
the mic is never touched. The full-screen-intent round trip is skipped entirely when foregrounded.

Two hard FFI rules: call `initBindings()` inside every isolate (statics are per-isolate, and missing
this yields **silent empty results**, not an exception), and pass pointers via `fromPtr`, not objects.

**Models** (all Apache-2.0, bundled as assets, copied out once via the official `copyAssetFile`):

| Purpose | Model | On disk |
|---|---|---|
| Wake word | `kws-zipformer-gigaspeech-3.3M-2024-01-01-mobile` | 4.99 MiB |
| Command ASR | `streaming-zipformer-en-20M-2023-02-17-mobile` (WER 3.94/9.79) | 33.54 MiB |
| **Total** | | **38.53 MiB** — *less than the 40 MB Vosk model it replaces* |

Wake phrase needs no retraining. The keywords line, generated by running the model's own
`bpe.model` through SentencePiece and validated against the shipped example file:

```
▁HE Y ▁LA Z Y :2.5 #0.18 @HEY_LAZY
```

Defaults (`keywordsScore: 1.0`, `keywordsThreshold: 0.25`) are known-weak for English BPE KWS
(upstream #2678 — no maintainer root-cause, only empirical tuning). The architecture defuses this:
run the wake word **hot** for recall and let the ASR adjudicate, since the pre-roll contains the
wake phrase — if the transcript doesn't start with something like "hey lazy", discard silently.

Endpointing: `rule2MinTrailingSilence: 0.72` (quantised to 40 ms steps; rule2 is the only rule gated
on having decoded speech, so it can't fire on background silence). Floor is 0.6 s before clipping
between clauses. Config traps: `modelType: 'zipformer'` (v1 recipe, **not** `zipformer2`),
`debug: false` (defaults true), `provider: 'cpu'`.

**No Silero VAD** — the recognizer's endpointing derives from transducer blank tokens, better
aligned than an energy gate; a second notion of "silence" would only disagree.

**Keep flutter_tts** — sherpa TTS is ~21 MB more, and a third ONNX session alongside a 4B GGUF is
what invites the low-memory killer at ~4 GB available.

### Phase 3 — Gemma 4 E2B alongside Qwen3-4B

Qwen3-4B stays in the catalog. Gemma 4 E2B is **added**, not substituted.

All verified 2026-07-31 against the Hugging Face API and the bundled native binaries:

| Fact | Value |
|---|---|
| License | **Apache-2.0** (Gemma 4 dropped the non-OSI Gemma Terms — this is what makes it viable) |
| Architecture | `gemma4`, 4,647,450,147 params total, ~2B active/token |
| Context | 131,072 |
| `gemma-4-E2B-it-Q4_0.gguf` | **2,841,481,184 B**, single file, HTTP 200 on the `resolve/main` URL |
| `gemma-4-E2B-it-qat-UD-Q2_K_XL.gguf` | 2,186,186,784 B — a standard llama.cpp K-quant, loadable by stock llama.cpp |
| MTP drafter `mtp-gemma-4-E2B-it-Q4_0.gguf` | 59,235,872 B, arch `gemma4-assistant` |
| llama.cpp support | `gemma4` landed at tag `b8637` (2026-04-02); llamadart 0.8.5 bundles `b9744` (2026-06-21). Symbols `llama_model_gemma4` and `llama_model_gemma4_assistant` confirmed present. **No version bump required.** |
| System role | **Supported** — the template handles `messages[0].role in ['system','developer']` and emits `<\|turn>system`. Unlike older Gemma, no folding into the first user turn. |
| `enable_thinking` | Template default `false`; llamadart default `true` → **must be set false explicitly** |

**Quant choice: Q4_0, not the smaller Q2.** `ggml-org` ships only Q4_0/Q8_0/BF16 for this model, and
llama.cpp's Adreno OpenCL kernels are tuned specifically for Q4_0 — the quant that costs 0.65 GB
more is also the one the GPU path is fastest on. Q2_K_XL is the fallback if memory pressure demands
it, accepting quality loss and losing the OpenCL optimisation.

**Memory caveat.** 10.9 GB physical, ~4 GB available at measurement. The ~12 GB ZRAM does *not*
rescue a large model: with `useMmap: true` the weights are file-backed clean pages, which the kernel
drops and re-faults from UFS rather than compressing into swap. The realistic failure mode is
**periodic stutter mid-answer**, not an OOM kill. Measure before treating Gemma 4 as default.

**MTP speculative decoding** is available on the llama.cpp path via
`SpeculativeDecodingConfig.mtp(draftModelPath:)` — the README's "rejected" language describes
LiteRT-LM rejecting llama.cpp knobs, not the reverse. Upstream reports only ~1–2 TPS gain on E-class
models, so this is a nice-to-have.

**Default selection is deferred to measurement.** "Stronger" and "faster" are separate axes, and
the assistant already escalates tool/action/internet turns to the server — the local model only
needs chit-chat and quick facts. Ship both, A/B on real prompts, then choose.

---

## Open decisions

1. **APK size.** Phase 2 measures at **+49 MB** (−8.6 Vosk, +20.7 onnxruntime, +4.2 sherpa,
   +33 models), taking the APK from 63 MB to ~113 MB — downloaded whole by the in-app updater every
   release. Alternatives: CTC-small ASR saves 9 MB but forfeits hotword biasing; bundling wake-word
   only keeps the APK at ~83 MB but reintroduces a runtime download.
2. **Wake word after swipe-away.** Vosk lives in Kotlin today and survives the app being swiped
   away. A Dart-owned KWS dies with the Flutter engine. v1 leans on the existing foreground
   re-arm; preserving current behaviour needs a headless Flutter engine in the service.

---

## Non-goals

- Dictation into other apps (an `InputMethodService`) — separate project.
- Replacing FUTO as the keyboard. FUTO's own stack (whisper.cpp + ACFT-finetuned Whisper + WebRTC
  VAD) is under the **non-OSI FUTO Source First 1.0** license and cannot be forked into MIT
  lazyclaw. Streaming Zipformer is also the better fit — Whisper is non-streaming, so it would
  still wait for the utterance to end.
- Replacing flutter_tts.

---

## Test strategy

New seams: `Speaker`, `SpeechCapture`, `LocalGenOptions`. New suites:

- `sentence_streamer_test` — including **feeding a reply one character at a time and asserting the
  emitted sequence is byte-identical to feeding it whole**. This is the property that makes
  streaming safe. Plus decimals, `Dr.`, initials, ellipses, Spanish `¿?`, Georgian (proves
  punctuation-driven, not capitalisation-driven), surrogate pairs, and `joined + flush == input`.
- `speech_queue_test` — serialisation, barge-in, error mid-stream, **hang** (a future that never
  completes), empty reply, dispose mid-queue, stale epoch. `FakeAsync` throughout.
- `streaming_tts_test` — asserts first audio is spoken **while the generation stream is still
  open**. Must fail on today's code.
- `needs_cloud_hazard_test` — sentinel-as-prefix escalates with nothing spoken; sentinel mid-reply
  does **not** escalate; sentinel split across three chunks still holds the arming gate.
- `endpointing_test` — no-speech settles; final-vs-timer race in both orders asserts exactly one
  `_ask`; `ListenFailedException` never leaves the phase stuck.
- `gen_params_test` — pins that the local chat tab still receives the 1024-token default.

**Fake fidelity is mandatory.** `_FakeEngine` throws `LocalLlmException` (the shape
`llamadart_engine.dart:92` actually throws); `_FakeCloud` adds `CloudTurnException` as a *stream
error*, not a synchronous throw; `_RecordingSpeaker` **throws if `speak()` is entered while another
is in flight** so an unserialised implementation fails loudly, and models an engine error as a
future that **never completes**, because that is literally what the plugin does.

---

## Build order

| Step | Content | Gate |
|---|---|---|
| −1 | Device unblock: mic ✅, battery ✅, assist app, download a model | end-to-end run possible for the first time |
| 0a | `LocalGenOptions` + prompt | worst-case generation 100 s → ~13 s |
| 0b | `SentenceStreamer` + `SpeechQueue` + `Speaker`, tests 5.1/5.2 | pure units green |
| 0c | Wire streaming into `_ask`, tests 5.3/5.4 | first audio before stream close |
| 0d | Bugs A–C + H behind the `SpeechCapture` seam, test 5.5 | no wedge states |
| 0e | Endpointing tuning + WebSocket re-dial fix | tail 3.6 s → ~1.5 s |
| 1 | OpenCL + llamadart 0.8.17 | on-device A/B; black-screen check |
| 2 | sherpa-onnx | continuous audio, no lost first second |
| 3 | Gemma 4 E2B added to catalog | measured A/B vs Qwen3-4B |

Steps 0a–0e need no new dependencies and address every CRITICAL defect.

---

## Unknowns to measure

Cold and warm `LlamadartEngine.load` time on the Mi 15; actual tok/s for both models on Oryon CPU
and on Adreno OpenCL; whether the Flutter engine survives a wake after activity destruction;
sherpa arm64 RTF (all published figures are desktop macOS); real "hey lazy" false-accept/reject
rates at the recommended thresholds.
