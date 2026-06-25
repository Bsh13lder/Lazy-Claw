# Hey Lazy — Tiered On-Device → Cloud Voice Assistant (Design Spec)

**Date:** 2026-06-25
**Status:** Design / awaiting review
**Surface:** LazyClaw Flutter app (`mobile/`) — Android first (Mi 15 / Android 16 / HyperOS)
**Companion research:** [`../research/2026-06-25-tiered-assistant-competitors.md`](../research/2026-06-25-tiered-assistant-competitors.md)

---

## 1. Vision

"Hey Lazy" is a **private-first phone assistant** that runs a small LLM **on-device** for quick, private turns, and **falls back to the real LazyClaw server** (FastAPI + ECO router → MiniMax/Claude, with web + MCP tools + the ability to *do things*) whenever a turn needs the internet, a tool, fresh facts, or to take a real action (add a task, log an expense, set a budget, send a message).

The endgame is **Apple Intelligence's tiered model — done more transparently**: on-device by default, escalate on need, and — unlike Apple, whose Private Cloud Compute escalation is *silent* — always show the user **where each turn ran** (a 🟢 On-device / 🟠 Cloud badge) and gate the first hand-off behind explicit consent.

This spec covers the App. The **keyboard** (a separate GPL fork) is out of scope here; it gets only a thin optional "max quality → call the server" toggle later.

### Design principles (LazyClaw-native)
- **On-device by default; cloud is opt-in.** Invert every big-provider default (cloud routing, training, retention all opt-in).
- **The local model must never fake a cloud capability.** Anything needing a tool / internet / state change is *hard-escalated* — never hallucinated locally.
- **License + privacy discipline.** No call-home dependencies (rules out Picovoice Porcupine); permissive engines only (sherpa-onnx Apache-2.0, openWakeWord Apache-2.0).
- **Reuse, don't rebuild.** Lean on the app's existing transport (`ChatSocket`), local engine (`LlamadartEngine`), STT/TTS, settings, and the `lib/ui/` design kit.

---

## 2. Scope & phasing

Built in three phases, each independently shippable + reviewable.

| Phase | Title | Delivers |
|---|---|---|
| **P1** | **Tiered brain** | The router (local↔cloud), `CloudTurnClient`, voice actions + confirmation, the privacy toggle + provenance badge + first-hop consent. *The headline.* |
| **P2** | **Google-style visual overlay** | The "Hey Google"-class animated assistant UI (launch morph, waveform, thinking orb, streamed reply, result card), emerald-branded, with the badge + mic indicator baked in. |
| **P3** | **"Hey Lazy" wake word** | Always-listening foreground-service wake detection (sherpa-onnx), the background→foreground handoff, and the MIUI survival wizard. |

### Explicitly deferred (own specs later)
- Fine-tuned route-token + outcome-mined training + log-prob confidence gating (research §6; P1 uses a heuristic router).
- Phone-notification reading ("what did I miss?") — a separate Android `NotificationListenerService` capability.
- Full data-retention picker, exportable Privacy Report, per-conversation no-save chat (P1 ships the toggle + badge + consent; the richer privacy surface is a follow-on).
- iOS wake word / over-other-apps overlay (iOS has no draw-over-apps; P1/P2 work in-app on iOS, P3 is Android-only).
- The keyboard's thin cloud toggle.

---

## 3. Architecture overview

### 3.1 The three tiers
- **🔒 Local** — on-device Qwen2.5-1.5B via `llamadart` (`LocalLlmEngine`). Private, offline, instant. Plain chat + simple stuff.
- **☁️ Cloud** — the real LazyClaw server agent over the app's existing `ChatSocket` (WebSocket, auto-reconnect, session-cookie auth). Full power: internet, MCP tools, and real actions.
- **Manual locks** — Privacy mode pins to Local; Max-quality pins to Cloud.

### 3.2 The mode enum (copied from Google's `InferenceMode`)
```dart
enum AssistantBackendMode { onlyOnDevice, preferOnDevice, preferCloud, onlyCloud }
```
Surfaced as a **3-way UI switch** (the 4th, `onlyCloud`, is implied by "Max quality" when offline-throw isn't wanted):

| UI label | Enum | Behaviour |
|---|---|---|
| **Local 🔒** | `onlyOnDevice` | Never leaves the phone. = "Process data only on device". |
| **Auto ⚡** (default) | `preferOnDevice` | Local-first; auto-escalate on need; cloud on capability mismatch. |
| **Max quality ☁️** | `preferCloud` | Straight to the server when online; local only when offline. |

### 3.3 Data flow (one turn)
```
STT (speech_to_text, on-device)  ──►  AssistantRouter.decide(turn, mode, deviceState)
                                            │
                  ┌─────────────────────────┴───────────────────────┐
                  ▼ LOCAL                                            ▼ CLOUD
        LlamadartEngine.generate()                       CloudTurnClient.streamTurn()
        (streams String tokens)                          (ChatSocket → TokenFrame stream)
                  └─────────────────────────┬───────────────────────┘
                                            ▼
                        reply stream → ReplyStreamView + flutter_tts (read-back)
                                            ▼
                              ProvenanceBadge: 🟢 On-device / 🟠 Cloud
```
Both paths already speak the same `TokenFrame`/`DoneFrame`/`ErrorFrame` shape (`LocalChatController` synthesizes them today), so the streaming UI is identical regardless of tier.

---

## 4. Routing (P1) — the MVP heuristic router

No model fine-tuning in v1. A **pure, unit-testable** Dart decision function. The research's load-bearing rule: **"needs a tool / action / internet / fresh facts" is a hard escalate** — that is exactly where the cloud's value lives and where the local model must never bluff.

### 4.1 Decision order
```
AssistantRoute decide(turn, mode, device):

  # STAGE 0 — hard gates (deterministic, ~0 cost)
  if mode == onlyOnDevice or device.privacyOnly:        return LOCAL   # honor the lock
  if mode == onlyCloud:                                  return CLOUD
  if not device.online:                                  return LOCAL  # offline → degrade gracefully
  if device.batteryCritical or device.thermalThrottling: return LOCAL  # warn: cloud paused
  if mode == preferCloud:                                return CLOUD

  # STAGE 1 — escalate-on-NEED (mode == preferCloud already returned; this is Auto)
  if needsCloud(turn):                                   return CLOUD  # the load-bearing rule

  # STAGE 2 — plain chat → local, escalate only if unsure
  if localSelfSignalsEscalate(turn):                     return CLOUD  # secondary, optional
  return LOCAL
```

### 4.2 `needsCloud(turn)` — the signal list (Stage 1)
Returns true if **any** fires. (Deliberately recall-biased: a false escalate just spends a cloud turn; a false *non*-escalate makes the local model bluff a capability it lacks — much worse.)

- **Action verbs** (state-changing / tool intent): `add, log, set, send, book, schedule, remind, pay, buy, search, look up, call, email, message, create, make, update, change, delete, remove, cancel, transfer, order, open` (+ Spanish equivalents: `añade, agrega, apunta, envía, manda, recuérdame, programa, paga, busca, llama, crea, borra, cancela…`).
- **Recency / world-knowledge markers**: `today, now, latest, current, this week, price of, cost of, weather, news, who won, when is, what time, stock, score, near me, open now`.
- **Explicit tool / internet intent**: mentions of email/WhatsApp/Instagram/Upwork/calendar/web/Google, "search the web", "look it up".
- **Context length** > ~4k tokens (the on-device ceiling per Gemini Nano's documented cap).

Each category is a `const` word-list so it is unit-testable and tunable. Markers are matched case-insensitively on the STT transcript; action verbs preferentially at the start of the utterance (imperatives).

### 4.3 `localSelfSignalsEscalate(turn)` — secondary (Stage 2)
Optional, cheap: the local system prompt instructs the model to emit a leading sentinel `[[NEEDS_CLOUD]]` if it cannot answer confidently or needs tools/data it lacks. If the first streamed line contains the sentinel, abandon the local generation and re-route to cloud. (This is a zero-fine-tune approximation of the research's Self-RAG route-token; the proper trained token + log-prob/perplexity gating is **deferred to v2**.)

### 4.4 Unit-test cases (router)
- `"add milk to my shopping list"` → CLOUD (action verb).
- `"what's the weather in Madrid today"` → CLOUD (recency).
- `"log a 20 euro expense for lunch"` → CLOUD (action).
- `"who won the match last night"` → CLOUD (recency + world-knowledge).
- `"what's a good metaphor for patience"` → LOCAL (plain chat).
- `"translate 'good morning' to Spanish"` → LOCAL.
- mode=onlyOnDevice + `"send a message to John"` → LOCAL (lock wins; the controller then tells the user this action needs cloud and is blocked by privacy mode).
- offline + `"search the web for X"` → LOCAL (degraded; controller explains it needs internet).
- mode=preferCloud + any → CLOUD (unless offline).

---

## 5. Cloud delegation (P1)

### 5.1 `CloudTurnClient` (new — `lib/chat/cloud_turn_client.dart`)
A thin wrapper over the **existing** `ChatSocket` (do not build new transport):
```dart
class CloudTurnClient {
  CloudTurnClient(this._socket);
  final ChatSocket _socket;

  /// Runs one turn against the server agent; yields reply tokens.
  Stream<String> streamTurn(String userText, {String? systemPrompt}) async* {
    _socket.send(encodeClientMessage(userText /*, system: systemPrompt */));
    await for (final frame in _socket.frames) {
      if (frame is TokenFrame) yield frame.content;
      else if (frame is DoneFrame) return;
      else if (frame is ErrorFrame) throw CloudTurnException(frame.message);
    }
  }
}
```
- Reuses `ApiClient` base URL (`ServerConfig.resolveBaseUrl()` — ngrok primary, LAN fallback), the `PersistCookieJar` session cookie, and `ChatSocket`'s auto-reconnect + offline outbox. **No new auth.**
- A cloud turn is a **normal server agent turn**, so the server's full skill registry (`add_task`, `add_expense`, `set_budget`, web search, MCP, `send_message`) is available with zero extra wiring.

### 5.2 Wiring in `LazyAssistantController._ask()`
Inject `CloudTurnClient`; route by `AssistantRouter.decide(...)`. On `preferOnDevice`, if the local path throws or emits `[[NEEDS_CLOUD]]`, transition phase → `thinking` ("Connecting to LazyClaw…") and continue on the cloud stream. Open question 4.1 below: fail-fast vs one retry on cloud error.

### 5.3 System prompt per tier
- **Local** — the existing device-optimized prompt (terse, "just answer, no roleplay", emoji/markdown stripped for clean TTS).
- **Cloud** — the standard server agent system prompt (the server owns it; the app just relays the user text).

---

## 6. Voice actions + confirmation (P1)

Cloud turns can *do things*. Confirmation is **risk-tiered by consequence + reversibility**, not by action type — the rule Apple, Amazon, and Google independently converged on.

### 6.1 Confirmation map
| Action | Tier | UX |
|---|---|---|
| `add_task`, `add_expense`, `set_budget`, create reminder | **Implicit** | Do it, then **read back one line**. No yes/no. |
| `send_message`, any delete / overwrite / cancel | **Explicit** | Read back full details + require a verbal "yes" before acting. |
| Money-movers (`upwork_accept_offer`, `upwork_submit_milestone`, `payment`) | **Server-gated** | The **server already** fail-closed ask-backs these. The app must **not** double-confirm — just surface the server's prompt. |

### 6.2 Read-back format
One line: verb + key slot(s). *"Logged €12 to groceries." / "Reminder set for 6 PM to call mom." / "Added milk to your shopping list."* Never replay the raw utterance. Suppress/shorten the spoken line when the overlay already shows it; voice-only must speak the full critical fact.

### 6.3 Repair, undo, disambiguation
- **Repair-at-read-back** — after the read-back, accept an immediate verbal correction ("no, make it €15") and re-fire instead of restarting the turn.
- **1-turn undo** — `add_task`/`add_expense`/`set_budget` are DB rows; keep the created row id in turn state and honor "undo that" for ~one turn. (`send_message` has no undo — kept in the explicit-confirm tier.)
- **Disambiguation** — when `send_message` resolves to >1 contact, **never auto-send to the first match**; ask one targeted "Did you mean John Smith or John Doe?" (cap reprompts at ~2 then bail). This reuses the existing `find_contact` flow server-side.

---

## 7. Privacy & transparency (P1) — the part that beats Apple

### 7.1 "Process data only on device" master toggle
Single switch, **default OFF** (capability-first), at the top of Hey Lazy settings (Samsung's pattern). ON ⇒ `mode` is forced to `onlyOnDevice`; cloud intents are surfaced as "needs the cloud — turn off on-device-only to use" rather than silently failing. Scope-labeled: "affects LazyClaw's assistant only."

### 7.2 Per-turn provenance badge
Every turn shows where it ran: 🟢 **On-device** pill (emerald) vs 🟠 **Cloud** pill (amber). This is the deliberate one-up over Apple's silent PCC escalation. Rendered in the overlay (P2) and any chat surface.

### 7.3 Live mic indicator (dual-encoded for accessibility)
Mic state by **both colour and shape**: filled emerald circle = live; hollow grey rounded-square = muted. (Colorblind-safe, mirrors iOS "Differentiate Without Color".)

### 7.4 First-cloud-hop consent
A one-time confirm before the very first escalation in `preferOnDevice`/`preferCloud`: *"Hey Lazy will send this turn to your LazyClaw account in the cloud to use the internet and tools. On-device turns stay on your phone."* Honest about the cloud safety-retention window (don't over-promise "nothing stored"). A standing "Confirm cloud requests" setting (default ON) governs the prompt; an "Ask cloud…" spoken phrase inlines consent for one turn. Any future attachment/media hop is a **separate, non-bypassable** consent tier.

---

## 8. Google-style visual overlay (P2)

Full "Hey Google"-class experience, emerald-branded. Brand: emerald `#10B981`, dark `#059669`, glow `#34D399`; cloud-badge amber `#F59E0B`; scrim `black @45%` + `ImageFilter.blur(18)`. All visuals consume `lib/ui/` tokens — no hard-coded colours.

### 8.1 State machine
| State | Visual | Transition | Duration |
|---|---|---|---|
| `launching` | Underlying app freezes + blurs; four emerald dots fly in and **morph into a 4-point sparkle** (negative-space reveal); edge-halo sweeps on. | Transparent route (`opaque:false`) + `Hero`/`AnimatedBuilder`; `TweenSequence` + `ShaderMask`; `easeOutBack`. | scrim 180ms · dots→sparkle 450ms · halo 600ms |
| `listening` | "Hi, how can I help?" crossfades to a centered **amplitude waveform** (audio-memo style); live partial transcript above; mic = filled emerald dot; halo breathes. | `AnimatedSwitcher`; `CustomPainter` waveform fed by `speech_to_text` sound-level stream. | crossfade 220ms · waveform 60fps · halo loop 2200ms |
| `thinking` | Waveform collapses to a **pulsing emerald radial orb**; halo intensifies + slow rotation. | bars → orb; `SweepGradient` rotation. | collapse 300ms · pulse 1400ms · rotate 8s |
| `speaking` | Orb emits ripples synced to TTS; reply **streams inline token-by-token**; provenance badge resolves (🟢/🟠); mic dims. | typewriter `StreamBuilder`; badge scale-in `easeOutBack`. | token ~20–40ms · badge 260ms |
| `result` | Reply settles into a **result card** (rounded 20px, emerald hairline, badge top-right); mic/keyboard affordances return for follow-up; halo calms to a thin rim. | `AnimatedContainer` lift + `SlideTransition`. | settle 280ms |
| `dismissed` | Card + halo + scrim slide/fade down; app unfreezes. | swipe-down / tap-scrim / back (`PopScope`). | 260ms |

Barge-in: tapping mic during `speaking`/`result` jumps to `listening` (stops TTS). Errors reuse the result card with an **amber** rim + retry.

### 8.2 Components (all first-party Flutter)
`AssistantOverlayController` (StateNotifier holding state + transcript + reply stream + mic + provenance) · `AssistantOverlayRoute` (transparent full-screen) · `BlurScrim` · `EdgeHaloPainter` (CustomPainter SweepGradient) · `DotsToSparkleMorph` · `LzSparkle` (reusable emerald sparkle, also the Home entry) · `VoiceWaveform` (CustomPaint from the mic amplitude stream) · `ThinkingOrb` · `SpeakingRipple` · `LiveTranscript` · `ReplyStreamView` · `ProvenanceBadge` · `MicStateIndicator` (dual-encoded) · `AffordanceBar` (mic + keyboard fallback) · `ResultCard` (extends existing `LzCard`) · `DismissGestureWrapper`.

### 8.3 In-app vs over-other-apps
- **In-app** (default): a transparent full-screen route — **no special permission**, full access to the loaded LLM + STT + TTS.
- **Over other apps** (optional setting): `flutter_overlay_window` runs in a *separate isolate* that **cannot** share the loaded GGUF in RAM, so it is only a **thin "Lazy is listening" pill** whose tap brings the real app forward (where the engines live). It never hosts mic/LLM. iOS has no equivalent — overlay-over-apps is Android-only.

---

## 9. "Hey Lazy" wake word + always-listening (P3)

### 9.1 Engine decision (resolved)
- **Primary: sherpa-onnx keyword spotting (k2-fsa), Apache-2.0.** 100% offline, **no AccessKey / no call-home**, ~3 MB, custom "hey lazy" via `keywords.txt` + `text2token` with **no model retraining**. The only option satisfying *all* constraints (on-device + permissive + redistributable + free + private). Integrated via a thin Flutter `MethodChannel`/`EventChannel` over the upstream Android Kotlin/JNI bindings.
- **Fallback: openWakeWord (Apache-2.0)** — needs an offline synthetic-TTS training pass for "hey lazy"; ONNX-Runtime-Mobile + platform channel.
- **Rejected: Picovoice Porcupine** — despite the best battery/DX, its AccessKey **calls home to validate the license**, enforces a 30-day device limit, and caps the free tier at **3 monthly active users**. That breaks both the privacy-first promise and the MIT/redistributable discipline. (Also rejected: DaVoice proprietary models; Snowboy/Coqui dead; Vosk = heavy full-ASR; Android `AlwaysOnHotwordDetector` = system-app only.)

> Note: this overrides the always-listening agent's battery-led Porcupine lean. The architecture below is engine-agnostic — sherpa-onnx runs inside the same foreground-service isolate where Porcupine would have.

### 9.2 Always-listening architecture
- **Foreground service** via `flutter_foreground_task`, `foregroundServiceType="microphone"` (Android 14+): a separate-isolate `TaskHandler` owns a single 16 kHz mono mic stream, runs the wake spotter continuously, owns the mandatory persistent notification, and exposes battery-optimization helpers + IPC (`sendDataToMain`/`sendDataToTask`). `stopWithTask` unset so it survives swipe-from-recents.
- **Single-mic-owner state machine** (the whole mic-contention story): exactly one of {wake detector, `speech_to_text`} holds the mic at any instant.
  `WAKE_LISTENING → (hit: stop spotter, release mic) → ACTIVATING → CAPTURING (speech_to_text) → ROUTING (LLM) → SPEAKING (flutter_tts) → resume → WAKE_LISTENING`.
- **Background→foreground handoff (Option A, chosen):** on a wake hit the service fires a `PendingIntent` to `MainActivity` deep-linked to `/assistant`, with **Background-Activity-Launch opt-in** (`setPendingIntentBackgroundActivityStartMode(ALLOWED)`). The persistent-notification tap is the always-available fallback when an OEM suppresses auto-launch. **Not** `USE_FULL_SCREEN_INTENT` (since Jan 2025 restricted to calling/alarm apps).
- **Master toggle "Hey Lazy always listening", default OFF (opt-in)** — OFF = no service, zero battery cost (assistant still works via ✨ tap / ASSIST gesture). Optional "pause when screen off" sub-toggle (default ON). This toggle is **orthogonal** to "process data on device only" (which only governs where the *recognized command* routes); UI copy must say so.

### 9.3 Manifest additions
`FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_MICROPHONE`, `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`, `RECEIVE_BOOT_COMPLETED`, optional `SYSTEM_ALERT_WINDOW`; the `flutter_foreground_task` mic service; keep `EnableImpeller=false` (the llamadart/Adreno black-screen fix). On **Android 15+ a `BOOT_COMPLETED` receiver may NOT start a mic FGS** — after a reboot the user must open the app once (call this out in the wizard).

### 9.4 MIUI / HyperOS survival wizard (load-bearing on the Mi 15)
MIUI **will** kill the service / block background mic unless the user hand-enables: **Autostart**, **Battery → No restrictions**, **Lock in recents**. There are **no APIs to set these** — we DETECT + DEEP-LINK only:
- Autostart deep-link (verified component): `com.miui.securitycenter / com.miui.permcenter.autostart.AutoStartManagementActivity` (via `android_autostart` or a platform channel); wrap every intent in try/catch → fall back to the app-details page.
- "No restrictions" battery state is **unreadable** via public API → use the unexpected-kill heuristic (`onDestroy` with `isTimeout=false` while the toggle is ON) to re-surface the wizard.
- A 3-card checklist (Autostart · Battery No-restrictions · Lock in recents), each with an "Open settings" deep-link + a screenshot/GIF + an "I did it" confirm. Shown when the master toggle is flipped ON; re-checked on every launch with a "listening is disabled by your phone" banner when detection fails.

---

## 10. Codebase integration map

**New files**
- `lib/assistant/assistant_backend_mode.dart` — `AssistantBackendMode` enum + `assistantBackendModeProvider` (persisted via `FlutterSecureStorage`).
- `lib/assistant/assistant_router.dart` — pure `decide(...)` + the signal word-lists (unit-tested).
- `lib/chat/cloud_turn_client.dart` — the `ChatSocket` wrapper.
- (P2) `lib/assistant/overlay/…` — the overlay controller + painters/components above.
- (P3) `lib/assistant/wake/…` — the wake service handler + platform-channel client; `android/` sherpa-onnx JNI glue + the MIUI wizard.

**Modified**
- `lib/assistant/lazy_assistant_controller.dart` — inject `CloudTurnClient`; route in `_ask()`; read-back; provenance.
- `lib/repositories/settings_repository.dart` (`GeneralSettings`) — add `assistantBackendMode`, `assistantProcessDataOnDevice`, `assistantAlwaysListening`; PATCH `/api/settings/general`.
- `lib/screens/settings_screen.dart` — "Hey Lazy" section (3-way mode picker + the two toggles, reusing `LzCard`/`LzButton`/`LzSwitch`).
- `lib/core/actions/app_actions.dart` + `deep_link_service.dart` + `MainActivity.kt` — wire the **ASSIST intent** (already in the manifest) to `AppAction.assistant` → `/assistant` (currently lands on `/home`).
- `android/app/src/main/AndroidManifest.xml` — P3 permissions + service.

**Reuse (unchanged):** `ChatSocket`, `ChatReducer`, `TokenFrame`/`DoneFrame`/`ErrorFrame`, `ServerConfig.resolveBaseUrl()`, `ApiClient` + `PersistCookieJar`, `LlamadartEngine`, `LocalModelStore`, `speech_to_text`, `flutter_tts`, the `lib/ui/` kit.

---

## 11. Settings model

`GeneralSettings` gains (all server-persisted via `PATCH /api/settings/general`, mirrored to a reactive provider):
- `assistantBackendMode: 'onlyOnDevice' | 'preferOnDevice' | 'preferCloud'` (default `preferOnDevice`).
- `assistantProcessDataOnDevice: bool` (default `false`; when true, forces `onlyOnDevice`).
- `assistantAlwaysListening: bool` (default `false`; gates the P3 foreground service).
- `assistantConfirmCloudRequests: bool` (default `true`; the first-hop consent prompt).

---

## 12. Testing strategy
- **Router** — pure-Dart unit tests over §4.4 (plus Spanish-verb cases). The router takes injected `mode` + `deviceState`, so no mocks of the network needed.
- **CloudTurnClient** — fake `ChatSocket` emitting `TokenFrame`/`DoneFrame`/`ErrorFrame`; assert token passthrough + error surfacing. *(Lesson: fakes must throw the **production** exception shape, or they green-light data-loss bugs.)*
- **Controller tiering** — fake engine + fake cloud client; assert `preferOnDevice` falls back on throw/`[[NEEDS_CLOUD]]`, `onlyOnDevice` never escalates, `preferCloud` goes straight to cloud.
- **Confirmation** — `send_message` requires explicit yes; `add_expense` reads back without a prompt; money-movers are not double-confirmed.
- **P2 overlay** — widget tests on state transitions (golden tests optional for the painters).
- **P3** — manual on-device (MIUI blocks automated install); verify the single-mic-owner invariant by inspection + the wizard deep-links resolve.

---

## 13. Open questions / decisions

1. **Tiered fallback policy** — on a cloud error in `preferOnDevice`, fail-fast (speak "couldn't reach the cloud") or one retry with backoff? *Proposed: fail-fast + a "try cloud again?" affordance.*
2. **Per-conversation tier override** — allow switching mid-session, or global-setting only for v1? *Proposed: global for v1; a long-press on the badge to override is a nice P2 add.*
3. **`[[NEEDS_CLOUD]]` self-signal** — ship in P1 or wait? *Proposed: ship it; it's cheap and recall-positive.*
4. **Custom "hey lazy" sherpa keyword FRR** — needs an on-device A/B vs an openWakeWord-trained model before locking the primary (tune `:boost`/`#threshold`). *P3 task.*
5. **iOS** — confirm P1+P2 are wanted on iOS (they work in-app); P3 wake word is Android-only.

---

## 14. Implementation order

**P1** (the brain): enum + provider → `assistant_router.dart` (+ tests) → `cloud_turn_client.dart` (+ tests) → controller wiring + read-back → settings (mode + on-device toggle + consent) → provenance badge + mic indicator (minimal, pre-P2) → ASSIST-intent routing. **Ship + verify on-device.**

**P2** (the visual): overlay controller + route + painters → wire the controller's phases to the existing assistant phases → fold the badge + mic indicator into the overlay. **Ship + verify.**

**P3** (wake word): sherpa-onnx JNI + platform channel → foreground service + single-mic state machine → BAL handoff → master toggle → MIUI wizard. **Ship + verify on-device.**

Each phase is its own implementation plan (writing-plans) with its own review + on-device check before the next.

---

## 15. Sources
Competitor/technical research with ~80 cited primary sources: [`../research/2026-06-25-tiered-assistant-competitors.md`](../research/2026-06-25-tiered-assistant-competitors.md). Design fan-out (codebase map, Google visual scope, wake-word engine eval, always-listening architecture) — 2026-06-25.
