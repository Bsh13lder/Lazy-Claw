# Tiered On-Device → Cloud AI Voice Assistant — Competitive & Technical Research

**Date:** 2026-06-25
**Purpose:** Inform the design of LazyClaw "Hey Lazy" — a Flutter Android voice assistant that runs a small local LLM (Qwen2.5-1.5B via llama.cpp) on-device for private/simple turns and escalates complex turns, anything needing internet/tools, or anything that performs real actions to the user's own FastAPI cloud agent (web search + MCP tools + add tasks/expenses/budgets).
**Method:** 6 parallel research agents, each doing live web search + fetch against primary sources (Apple/Google/Samsung/Amazon developer + security docs, OpenAI/Anthropic help centers, arXiv, AOSP). Marketing fluff excluded; third-party / unverified claims flagged inline.

The three load-bearing design questions — **routing decision logic**, **privacy/consent UX when data leaves the device**, and **voice-action confirmation patterns** — are prioritized throughout. The synthesized routing algorithm (Area 6) and the "Top 8 patterns to adopt" section at the end are the actionable core.

---

## 1. Apple Intelligence (the canonical tiered model)

A tiered on-device → cloud system that defaults to a small local model and escalates only when a request needs more capability, broader world knowledge, or carries data that must leave the device under explicit consent.

### 1.1 The tiered routing architecture

- **Tier 0 — on-device foundation model (~3B params)** [Apple primary]. One frozen ~3B base model, aggressively quantized (2025 decoder weights at ~2 bits/weight via QAT; 2024 averaged 3.7 bpw), with KV-cache sharing (5:3 depth split → 37.5% less KV memory) and **per-task LoRA adapters** (~10s of MB each) that recover quantization loss AND specialize per feature.
- **Tier 1 — on-device "Advanced" model with per-prompt routing (WWDC 2026)** [Apple primary]. A 20B sparse model activating just 1–4B params per request via Instruction-Following Pruning. Load-bearing quote: *"Because NAND-to-DRAM bandwidth is too slow to swap weights token by token… AFM 3 Core Advanced makes routing decisions **per prompt**."* A small predictor reads the prompt, picks experts, commits for the whole generation.
- **Tier 2 — Private Cloud Compute server model** [Apple primary]. A larger Parallel-Track MoE on Apple-silicon servers. Requests route here "when tasks require more computational power" (capability escalation, not default) [third-party].
- **Tier 3 — ChatGPT (OpenAI)** [third-party: Apple Support/MacStories]. Triggered for world knowledge outside Siri's domain + open-ended composition. "What's the capital of Italy?" stays local; "…has it always been the capital?" escalates. The phrase "Ask ChatGPT…" bypasses the router straight to this tier.
- **The Orchestrator + two side databases** [third-party, well-attested]. An on-device Orchestrator "determines how to best handle a user request, whether through on-device models, Apple's server models or ChatGPT," consulting a **semantic index** (on-device RAG over the user's files) and an **App Intents toolbox** (catalog of what each app can DO). Cited routing signals: task complexity, privacy sensitivity, device thermal/battery, biased to stay on-device for anything private/latency-sensitive.

> **Honesty caveat:** Apple publishes model internals (sizes, quantization, LoRA, PT-MoE, per-prompt routing) as primary sources, but does NOT publish exact *cross-tier* decision thresholds. The "Orchestrator + complexity/privacy/thermal" signal set is assembled from WWDC summaries + third-party analysis. The one explicit Apple routing quote is *intra-model* ("routing decisions per prompt").

**Adopt this:** A thin on-device router that decides ONCE per utterance (not per token) from {complexity estimate, privacy flag, latency target, battery/thermal, adapter+tool availability}; default to a sub-4-bit local model with hot-swappable per-intent adapters; reserve the server tier for capability escalation. Our skill registry = the App Intents toolbox; our lazybrain/memory = the semantic index — the router should consult both before picking a tier.

### 1.2 Private Cloud Compute — security/transparency + what the user sees

- **Stateless / no-retention, mechanically enforced** [Apple primary]: *"This data must not be retained… after the response is returned."* The Secure Enclave randomizes the data-volume encryption keys on every reboot and never persists them, so a reboot makes prior on-disk state cryptographically unrecoverable.
- **Verifiable transparency / attestation** [Apple primary]: *"User devices will be willing to send data only to PCC nodes that can cryptographically attest to running publicly listed software."* The device wraps its request key only to nodes whose attested measurements match an entry in an **append-only public transparency log** ("cannot be removed without detection"). End-user phrasing: the device *"will refuse to talk to a server unless that server's software has been publicly logged for inspection."*
- **Non-targetability** [Apple primary]: load balancer has no user/device identity; a single compromised node decrypts only a small portion of requests.
- **Published for inspection** [Apple primary]: full PCC Security Guide, a Virtual Research Environment, source on GitHub (`apple/security-pcc`), and a bounty up to **$1,000,000** (RCE).
- **CRITICAL UX finding — escalation is automatic and SILENT** [Apple primary]: there is **no real-time on-screen indicator** when a request goes to PCC. The only surface is a retrospective, opt-in audit log: Settings → Privacy & Security → **Apple Intelligence Report** (last 15 min default, or last 7 days; JSON export). Apple's trust signal is cryptographic, not visual.

**Adopt this:** Make our server tier stateless by contract (in-memory only, no request logging, per-request key destroyed on completion) and publish that verbatim. Approximate attestation cheaply: expose `/build-manifest` (commit hash + image digest), publish digests to an append-only log (Sigstore/Rekor), and have the Flutter client refuse to send unless the server's attested digest matches a published entry. **Do BETTER than Apple:** show a live on-device/cloud chip the moment a query escalates AND keep an exportable per-request audit log — Apple has the log but lacks the real-time cue.

### 1.3 ChatGPT escalation — explicit per-request consent UX

- **Per-request consent by default** [Apple primary]: without explicitly asking for ChatGPT, the request is analyzed first; if ChatGPT would help, *"Siri will ask whether you would like to use ChatGPT."* Governed by the **"Confirm ChatGPT Requests"** toggle. Saying "Ask ChatGPT…" = inline consent skipping the card for that turn.
- **Settings toggle** [Apple primary]: Apple Intelligence & Siri → Extensions → ChatGPT → Confirm ChatGPT Requests. The extension is **off by default**; once enabled, "Confirm Requests" defaults **ON**.
- **Attachments = hard, non-bypassable consent gate** [Apple primary, strongest pattern]: *"You're always asked before any photos or files are sent to ChatGPT."* Even with "Confirm Requests" OFF, file/photo/PDF sends still force confirmation. Two tiers: text (toggle-skippable) vs attachment (always confirmed, no opt-out).
- **OpenAI-side guarantees** [Apple primary, verbatim]: *"Your IP address is obscured from ChatGPT"*; anonymous by default, no storage, no training, no info tied to your Apple Account. **Account-linking is opt-in and REVOKES these** (OpenAI may then log + train).

> **Caveat:** No primary source quotes the exact confirm-button glyph; the card's existence + per-request behavior are Apple-confirmed but the literal string isn't. The fully-confirmed non-bypassable disclosure is the attachment gate.

**Adopt this:** Default the external-cloud path to per-request consent (with an "Ask Cloud…" phrase that inlines consent for one turn); ship one "Confirm Requests" toggle defaulting ON; make ANY request carrying media/docs a SEPARATE non-bypassable consent tier that names the attachment and ignores "don't ask again"; default the cloud path to anonymous (IP stripped/proxied, no-store + no-train) and warn at link-time that connecting an account disables those guarantees.

### 1.4 Siri → App Intents for state-changing ACTIONS

- **Voice → intent mapping** [Apple primary]: an `AppIntent.perform()` does the work; `AppShortcut(intent:phrases:…)` binds spoken phrases, with `negativePhrases` to suppress false matches. App Shortcut params must be `AppEnum`/`AppEntity` with a fixed value set ("no open-ended values"). Semantic matching beyond literal phrases uses **App Intent Domains / assistant schemas** (`AppIntent(schema: .mail.createDraft)`).
- **Parameter resolution / disambiguation** [Apple primary], three modes thrown from inside `perform()`: `requestValue(dialog:)` (missing slot), `requestDisambiguation(among:dialog:)` (N candidates), `requestConfirmation(for:dialog:)` (one guessed value → yes/no). Prompts declared on the `@Parameter` itself.
- **Confirmation before a state change** [Apple primary]: a single awaited gate that **throws-and-aborts on deny**: `requestConfirmation() async throws` — *"Call this method before performing any work that might be destructive or unsafe… returns normally if they confirm, but throws an error if they cancel."* `ConfirmationActionName` labels the button AND encodes semantics (built-in verbs `buy`/`pay`/`send`/`delete`/`book`… plus `custom(…, destructive:)`).
- **Read the result back** [Apple primary]: `perform()` returns `.result(dialog:)` / `.result(value:dialog:view:)` — every action speaks/shows what it did.
- **Confirm vs run silently** [Apple primary]: no single "alwaysConfirm" flag — confirmation is opt-in per action by *calling* `requestConfirmation(...)`, tied to "destructive or unsafe" work. Read-only query intents skip the call and just return a read-back.

> **Caveat:** `needsValueConfirmation` (asked in the brief) could not be verified as a real Apple symbol — the real APIs are `requestConfirmation(for:dialog:)` + `@Parameter requestValueDialog:`.

**Adopt this:** Tag each intent with `mutatesState`/`destructive` booleans (our money-mover ask-back already does this conceptually). Read-only intents → run + read back; state-changers → a mandatory awaited confirmation gate that THROWS to abort on deny, labeled with a typed verb (pay/send/delete) carrying a destructive flag, and always return a spoken read-back. Resolve missing params with the three-mode pattern, carrying prompt text declaratively per parameter.

**Sources (Area 1):** machinelearning.apple.com/research/introducing-apple-foundation-models · /apple-foundation-models-2025-updates · /introducing-third-generation-of-apple-foundation-models · arxiv.org/abs/2507.13575 · developer.apple.com/wwdc26/guides/apple-intelligence/ · security.apple.com/blog/private-cloud-compute/ · /blog/pcc-security-research/ · /documentation/private-cloud-compute · apple.com/legal/privacy/data/en/intelligence-engine/ · support.apple.com/guide/iphone/apple-intelligence-and-privacy-iphe3f499e0e/ios · apple.com/legal/privacy/data/en/chatgpt-extension/ · support.apple.com/guide/iphone/use-chatgpt-with-apple-intelligence-iph00fd3c8c2/ios · developer.apple.com/documentation/appintents/appintent/requestconfirmation() · /intentparameter/requestvalue(_:)-592nd · /intentparameter/requestdisambiguation(among:dialog:) · /confirmationactionname · /intentresult · /appshortcut · /app-intent-domains · developer.apple.com/videos/play/wwdc2022/10170/ · help.openai.com/en/articles/10263570-apple-intelligence-siri-faq · venturebeat.com/technology/on-device-ai-agents-hit-a-hard-memory-limit (third-party)

---

## 2. Google / Gemini — on-device vs cloud

The single most adoptable finding: Google ships a **named four-state routing enum** (`InferenceMode`) plus a **four-state availability lifecycle** (`FeatureStatus`). Both port directly.

### 2.1 Gemini Nano on-device (AICore / ML Kit GenAI)

- **Architecture:** Nano runs inside **AICore** (an Android system service owning distribution, updates, HW acceleration, request isolation). All apps share ONE Nano base; per-feature behavior is a tiny downloaded **LoRA adapter** on top — once Nano exists, enabling a feature is a fast adapter fetch. AICore has no direct internet and stores no input/output after processing.
- **Capabilities & limits (concrete):** APIs = Summarization, Proofreading, Rewriting, Image Description, Speech Recognition, Prompt (text/multimodal). **Input cap ~4000 tokens (~3000 words)**; `setLongInputAutoTruncationEnabled(boolean)`. Variants nano-v2 / nano-v3 (v3 on Pixel 10). Runtime guards: `ErrorCode.BUSY`, `BACKGROUND_USE_BLOCKED` (must be foreground), `PER_APP_BATTERY_USE_QUOTA_EXCEEDED` (daily battery budget).
- **Availability lifecycle (key pattern):** before any AI UI, call **`checkFeatureStatus()`** → `UNAVAILABLE` / `DOWNLOADABLE` / `DOWNLOADING` / `AVAILABLE`. `DOWNLOADABLE` → `downloadFeature(callback)` with progress; `AVAILABLE` → `runInference(request)`. Skipping download → first inference lazily triggers it.
- **Documented on-device→cloud fallback:** Firebase AI Logic hybrid inference — one `generativeModel(...)` handle takes `OnDeviceConfig(mode = InferenceMode.PREFER_ON_DEVICE)`; cloud default `gemini-2.5-flash-lite`. On-device qualifies on Pixel 6+ and growing Samsung set.

**Adopt this:** Mirror the two-enum design — an availability gate checked *before* showing on-device UI, plus a shared-base + thin-adapter model so the local tier is "warm" once downloaded; treat ~4k-token input as the on-device ceiling that forces a cloud hop.

### 2.2 The routing enum — `InferenceMode` (PRIORITY)

Four explicit modes on one model object, with spelled-out fallback semantics:
- **`PREFER_ON_DEVICE`** — on-device if available, else cloud; ALSO auto-routes to cloud for any request on-device can't handle (unsupported op), no error.
- **`PREFER_IN_CLOUD`** — cloud when online, on-device only when offline.
- **`ONLY_ON_DEVICE`** — on-device or throw.
- **`ONLY_IN_CLOUD`** — cloud or throw.

On-device vs cloud-only is enumerated: on-device = single-turn text, text+image (PNG/JPEG), structured output. Cloud-only = multi-turn chat, function calling / code execution, audio/video/PDF, image generation. Google's custom-routing guidance says weigh **network latency, device health (battery + processor load), and query complexity**.

**Adopt this:** Copy `InferenceMode` verbatim as our tier-selection enum (`preferOnDevice / preferCloud / onlyOnDevice / onlyCloud`), default `preferOnDevice`, and route to cloud on **capability mismatch** (long context, multi-turn, tool-calling) in addition to availability, factoring battery/network/complexity.

### 2.3 Gboard / Messages on-device vs cloud (privacy split)

- **Gboard Smart Reply** runs **on-device via Gemini Nano** (since Pixel 8 Pro) — nothing sent to cloud; privacy is the headline.
- **Magic Compose (Google Messages)** is **cloud** — sends up to **20 prior RCS messages** to the server to generate suggestions (not stored long-term / not training). Voice typing, translation, Smart Compose also cloud.
- Product lesson: lightweight latency-sensitive privacy-first suggestions stay on-device; anything needing a bigger model goes cloud **with an explicit "we send N recent messages" disclosure.**

**Adopt this:** Tier the same way — quick replies / proofread / rephrase stay on-device and advertised "processed on your phone"; escalate only heavier asks, and surface a one-line "sends your last N messages" disclosure exactly like Magic Compose.

### 2.4 Voice → in-app action: App Actions (legacy) → AppFunctions (current)

- **Historical (App Actions, legacy):** apps declared `<capability>` in `shortcuts.xml` mapping a **Built-In Intent (BII)** (e.g. `ORDER_MENU_ITEM`) to fulfillment = explicit Android intent or deep-link `<url-template>`. Foreground-app invocation let a BII match without naming the app. Confirmation was handled by Assistant launching the app to a specific screen for the user to complete the state-changing step in-app. (Conversational Actions deprecated 2023-06-13; BII surface wound down.)
- **Current (AppFunctions, Android 16 — the "on-device MCP"):** apps annotate methods with `@AppFunction(isDescribedByKDoc = true)` (KDoc becomes the agent-facing tool description); callers holding `EXECUTE_APP_FUNCTIONS` discover/invoke via `AppFunctionManager`. Google frames it as *"the mobile equivalent of tools within MCP"* — apps as on-device MCP servers. **State-change confirmation pattern:** functions are `suspend` and **return the final mutated object** so the caller verifies and surfaces the result rather than firing blind.

**Adopt this:** Model the voice→action layer as MCP tools (we already do) with **KDoc-style self-describing descriptions** and a **return-the-final-state contract** for every mutating action, so the brain confirms by echoing the resulting object — mirrors AppFunctions and slots into our skill-registry + checkpoint flow.

**Sources (Area 2):** developer.android.com/ai/gemini-nano · developers.google.com/ml-kit/genai · /ml-kit/genai/summarization/android · android-developers.googleblog.com/2025/08/the-latest-gemini-nano-with-on-device-ml-kit-genai-apis.html · firebase.google.com/docs/ai-logic/hybrid-on-device-inference · developer.android.com/ai/hybrid · 9to5google.com/2024/01/29/gboard-smart-reply-gemini-nano-apps/ · security.googleblog.com/2020/10/privacy-preserving-smart-input-with-gboard.html · androidpolice.com/google-messages-ai-magic-international-rollout/ · developer.android.com/ai/appfunctions · /ai/appfunctions/add-appfunctions · 9to5google.com/2026/02/25/android-appfunctions-gemini/ · developer.android.com/develop/devices/assistant/action-schema · /develop/devices/assistant/intents · developers.google.com/assistant/app/foreground-app

---

## 3. Samsung Galaxy AI — "Process data only on device"

Samsung shipped a hard privacy boundary the user can flip, at OS scale (One UI 6.1 → 7 → 8).

### 3.1 The toggle — location, scope, exact string

- **Exact UI string:** `Process data only on device` (single switch). [Samsung support + 9to5Google + SamMobile + Android Authority]
- **Settings path:** One UI 7/8 → `Settings → Galaxy AI → Process data only on device` (bottom of screen). One UI 6.1 → `Settings → Advanced features → Advanced intelligence → Process data only on device`.
- **Scope: ONE global master switch, not per-feature.** Samsung Knox enterprise docs literally call it a *"master switch" to disable all cloud-based processing for the Galaxy AI features* (single boolean).
- **Default state: OFF** (cloud allowed) — framed as an opt-in privacy lockdown.
- **Scope caveat Samsung surfaces:** *"this toggle only affects Samsung's own AI features"* — third-party / Google AI features are out of scope.

**Adopt this:** Ship a single `Process data only on device` toggle at the TOP of voice-assistant settings (default OFF = full capability), and scope-label it ("affects LazyClaw's own AI only").

### 3.2 On-device vs cloud feature split (One UI 7)

- **Survives the toggle (on-device only):** Call Assist Live Translate, Interpreter, Transcript Assist, Audio Eraser, ambient wallpaper, Health Assist; *partial* — Writing Assist (chat translation, style/grammar, suggested replies), Note Assist (translate, transcribe), Browsing Assist (translate).
- **Dies when toggle ON (cloud-only):** Writing Assist (Composer, Summarize, Organize), Note Assist (auto-format, summarize, spell/grammar, generate cover, sketch-to-image), Browsing Assist (summarize, read aloud), Photo Assist (everything), Drawing Assist (everything).
- **Load-bearing insight:** the line is roughly **transform/translate-in-place = on-device** vs **generate/summarize/synthesize = cloud.**

**Adopt this:** Tag every intent with a `processingTier` (`onDevice` | `cloudRequired`). When the toggle is ON, keep on-device intents live (wake word, STT/TTS, simple commands, translation) and only gray out cloud intents — **degrade per-action, never kill the whole assistant.**

### 3.3 Transparency / consent / watermarking

- **Consent framing** [Samsung newsroom]: *"all AI experiences are designed with privacy in mind — even those that utilize remote servers,"* and *"personal data is never stored long-term or used for AI training — whether processed on-device or on the cloud."*
- **Per-feature cloud disclosure** [SamMobile]: for some features *"some information may be sent to Samsung's cloud servers,"* with the on-device toggle as the explicit opt-out.
- **Disclosure-consistency trap (a finding):** Samsung Knox enterprise doc says *"Features that process data in the cloud may be used for model training,"* contradicting the consumer "never used for training" line. **Keep our one promise true everywhere.**
- **Watermarking** [Samsung support]: *"A Galaxy AI watermark will appear on AI-generated images"* — visible sparkle-logo, bottom-left. Caveat: removable by Samsung's own erase tool → advisory, not tamper-proof.

**Adopt this:** Show a one-line "this request will be processed in the cloud" disclosure inline the first time a query routes off-device, plus a standing plain-language data promise in settings; visibly tag AI-generated output (treat watermarks as advisory).

### 3.4 The toggle UX as a design pattern (priority)

- Trade-off communicated as one-tap simplicity ("Managing your privacy is as simple as tapping a button"), with the consequence surfaced on the SAME screen ("which AI features will keep working offline").
- Default = capability-first (OFF); privacy = opt-in; the cost ("you lose summaries, generative edit, composing") is shown, not hidden.
- Graceful degradation, not a dead end — unsupported features are simply limited; partial features keep their local sub-actions.

**Adopt this:** Single-tap toggle with an inline live list right under it ("Stays on-device: wake word, dictation, translation, simple commands · Becomes unavailable: long-form summaries, generative answers"). Default OFF, instantly reversible, never throws errors in on-device mode.

**Sources (Area 3):** samsung.com/us/support/answer/ANS10000753/ · docs.samsungknox.com/admin/knox-platform-for-enterprise/knox-service-plugin/configure-advanced-policies/data-processing-for-galaxy-ai/ · samsung.com/ae/support/mobile-devices/how-to-distinguish-between-on-device-functions-and-cloud-based-functions… · samsung.com/us/support/answer/ANS10000934/ · news.samsung.com/us/your-privacy-secured-galaxy-ai-empowers-you-take-control-your-data · 9to5google.com/2025/02/20/how-to-turn-on-galaxy-ai-on-device-processing/ · sammobile.com/news/use-galaxy-ai-without-sending-data-to-samsung-heres-what-you-lose/ · androidauthority.com/samsung-galaxy-ai-on-device-only-3488445/ · slashgear.com/2002554/samsung-galaxy-one-ui-vs-android-16-ai-choice/ · gizmodo.com/galaxy-s24-ai-removes-ai-watermark-phones-photos-1851180966
*(Samsung newsroom page timed out on direct fetch; its quotes corroborated via search snippets from the same Samsung domain — flagged.)*

---

## 4. Voice-Action Confirmation Patterns

Apple, Amazon, and Google independently converged on the SAME risk-tiered rule: gate state changes on **consequence and reversibility**, not on action type. This is the single most important pattern to adopt.

| Risk tier | Trigger | UX | Who |
|---|---|---|---|
| **High → explicit confirm** (yes/no before acting) | Sends to others, costs money, deletes/overwrites, hard to undo | Read back full details + require verbal "yes" | All three |
| **Low → run silently + implicit read-back** | Benign, reversible, high-confidence parse | Do it, then echo what you did. No yes/no | All three |
| **Ambiguous → disambiguate first** | Multiple plausible matches | Targeted "which one?" before acting | All three |

### 4.1 Confirmation — which actions confirm vs run silently

- **Apple:** `AppIntent.requestConfirmation()` *"before any work that might be destructive or unsafe… throws an error if they cancel."* HIG rule: *"only use this step for consequential actions — a financial transaction, a destructive action like deleting content, or an action that may feel high risk like sending a calendar invite to a big group. Use these appropriately but sparingly."*
- **Amazon (most formalized):** **explicit confirmation** *"require[s] verbal approval… protects against high-consequence failures such as monetary impact, data lost."* **Implicit confirmation** *"re-state[s] what customers said but doesn't ask them to confirm… use when the likelihood of a mistake is low and consequences minor."* Rule verbatim: *"confirm things when a mistake could be inconvenient — performing an action that affects others (sending a text) or buying something."* Plus the guard: *"use confirmation sparingly."* Declarative: `"confirmationRequired": true` + `Dialog.ConfirmSlot`/`ConfirmIntent`.
- **Google:** *"Double-check prior to performing an action that would be difficult to undo — deleting user data, completing a transaction."* and *"Don't confirm if the input is simple and typically recognized with high confidence."* App Actions hard requirement: don't modify real-world state *"without first confirming."*

**Adopt this:** Tag each voice action with a declarative `confirm: explicit | implicit` flag. **`send_message` → explicit confirm** (all three name messaging specifically). **`add_expense` / `set_budget` / `add_task` → implicit read-back only** (reversible, personal, benign). **Any delete/overwrite → explicit confirm.** Keep the "confirm sparingly" guard so we never gate a benign task-add behind a yes/no.

### 4.2 Disambiguation ("did you mean")

- **Apple:** `requestDisambiguation(among:dialog:)` — *"disambiguate amongst an array of values"* (e.g. "Which author?").
- **Amazon:** explicit Don't — *don't auto-pick* when >1 plausible match; ask ("Washington, D.C., or Washington State?"). `Dialog.ElicitSlot` + Entity Resolution.
- **Google:** lists for disambiguation (2 contacts named Peter → "Peter Jons or Peter Hans?", min 2 / max 30). Cap reprompts at ~2 then exit gracefully.

**Adopt this:** When `send_message` resolves to >1 contact, **never auto-send to the first match** — ask one targeted "Did you mean John Smith or John Doe?" Same for "add to *which* list/project." Cap retries ~2 then bail. Highest-value safety check for our messaging path.

### 4.3 Read-back of the result (implicit confirmation)

The default success behavior for low-risk actions across all three: **do it, then echo what was done — no yes/no.**

- **Apple:** return `.result(dialog: "…")`; voice-only must contain all critical info, but suppress spoken dialog *"when it is fully redundant with your visual response."*
- **Amazon:** implicit confirmation IS the read-back ("Okay. You're leaving June ninth…"); money always read back exactly before the yes/no.
- **Google:** *"Don't belabor confirmations by focusing on what your Action heard"* — echo the information, not the act of hearing.

**Adopt this:** Every successful state change gets a one-line spoken read-back: verb + key slot(s) — *"Added milk to your shopping list." / "Logged €12 to groceries." / "Reminder set for 5 PM."* Never replay the raw utterance; suppress/shorten the spoken line when the screen already shows it; voice-only must speak the full critical fact.

### 4.4 Undo / error recovery

Honest finding: **none of the three ships a universal post-commit "undo last action" voice primitive.** The model is *prevent* (confirm before risky acts) + *repair at the confirmation turn* (deny → re-elicit).

- **Apple** has the closest thing: the `UndoableIntent` protocol registers actions on the system `UndoManager` ("People are more likely to try things if they know they can change their mind").
- **Amazon / Google:** no global undo; recovery = deny the confirmation → re-elicit corrected slot, with escalating reprompts (Amazon: *"Don't blame the customer"*).

**Adopt this:** Two layers. (1) **Repair-at-read-back** — after the spoken read-back, accept a quick verbal correction ("no, make it €15") and re-fire instead of restarting. (2) **A real 1-turn undo** for reversible DB actions — `add_task`/`add_expense`/`set_budget` are all DB rows, so keep the just-created row id in turn state and honor "undo that" for ~one turn (mirrors `UndoableIntent`). For `send_message`, prevention is the only net — keep it in the explicit-confirm tier.

> **Caveats:** `needsValueConfirmation` could not be confirmed as a real Apple symbol; the Google "milk added" line is a paraphrase of the documented implicit-confirmation principle, not a verbatim example.

**Sources (Area 4):** developer.apple.com/documentation/appintents/appintent/requestconfirmation() · /intentparameter/requestconfirmation(for:dialog:) · /intentparameter/requestdisambiguation(among:dialog:) · /intentresult/result(dialog:) · /undoableintent · developer.apple.com/videos/play/wwdc2022/10032/ · /10169/ · developer.amazon.com/en-US/docs/alexa/custom-skills/define-the-dialog-to-collect-and-confirm-required-information.html · /custom-skills/dialog-interface-reference.html · developer.amazon.com/en-US/alexa/alexa-haus/design-principles/be-trustworthy · /patterns-and-components/patterns-lists · developers.google.com/assistant/conversation-design/confirmations · /conversation-design/errors · developer.android.com/develop/devices/assistant/intents

---

## 5. Privacy / Incognito / Transparency

### 5.1 Private / incognito / no-save mode

A per-conversation "ephemeral" toggle that disables history, memory, and training — but keeps a short safety-retention window.

- **ChatGPT "Temporary Chat":** *"won't appear in your history, and ChatGPT won't remember anything,"* not used to improve models, no Memory access. Catch: OpenAI *"may still keep a copy for up to 30 days"* for abuse monitoring. Critical nuance — turning off Memory/personalization *"does not disable safety features."*
- **Gemini "Keep Activity off":** conversations not saved to Apps Activity, but Google still saves a copy *"for up to 72 hours"* for service/feedback.
- **Claude "Incognito":** ghost icon (third-party reporting, verify in-product); incognito chats *"not used to improve Claude's models, even if the main training toggle is on."*

**Adopt this:** Ship a per-conversation "Private chat" toggle (ChatGPT model) disabling local history + cloud sync + training, but state plainly that a short safety-retention window may apply when the cloud tier is used — and that **on-device-only turns leave nothing behind at all** (our genuine edge).

### 5.2 Data-retention controls

Three controls — (a) training opt-out toggle, (b) configurable auto-delete window, (c) delete-all — with an explicit non-zero backend grace period.

- **ChatGPT:** "Improve the model for everyone" toggle; deleted chats follow a 30-day backend window.
- **Gemini (strongest auto-delete UX):** keep activity for **3 / 18 / 36 months or never**; **default = auto-delete after 18 months**; plus "Turn off and delete."
- **Claude:** training toggle with asymmetric retention — allow training → **5 years**; opt out → **30-day** standard. Changes apply only to new/resumed chats.

**Adopt this:** A retention picker modeled on Gemini (auto-delete after 30 days / 6 months / 18 months / never) + one-tap "Delete all history" + a single clearly-worded training opt-out. Because we're on-device-first, **default to never-send-for-training; make cloud training opt-in, not opt-out.**

### 5.3 Transparency indicators — data leaving the device / cloud processing

- **OS hardware-sensor indicators (gold standard):** iOS — orange dot = mic, green = camera(+mic); with "Differentiate Without Color" the orange dot becomes an **orange square** (accessibility). Android (AOSP) — status-bar icon → dot; tap shows which sensor; **Privacy Dashboard** = 24h timeline.
- **Cloud-vs-on-device transparency log:** **Apple Intelligence Report** (Settings → Privacy & Security) — last 15 min / 7 days, lists *"requests sent off your device and processed by Private Cloud Compute"* + ChatGPT requests; exportable as `.json`.
- **Per-feature on-device disclosure:** *"your device will indicate in Siri Settings whether the things you say are processed on your device and not sent to Apple servers."*

**Adopt this:** Two indicator layers, both needed — (1) a live capture indicator during voice (OS-dot style, color + shape fallback for accessibility), and (2) a per-turn badge of where it ran: green **"On-device"** pill vs amber **"Cloud"** pill — plus an exportable, time-windowed **Privacy Report** log (Apple Intelligence Report model) listing which turns left the device and to which provider.

### 5.4 Labeling AI-generated content & source attribution

- **Third-party attribution in the request flow:** Apple's ChatGPT integration — *"asks before any of your information is shared,"* surfaced via the "Ask ChatGPT" phrase; otherwise Siri prompts whether to use ChatGPT.
- **Provenance + invisible watermark (dual-layer):** OpenAI images carry BOTH C2PA Content Credentials (signed, tamper-evident, travels with the file) AND a SynthID invisible pixel watermark. Google's SynthID watermarks >20B pieces; newer images add C2PA; verification is conversational ("Was this created with Google AI?").

**Adopt this:** (1) Show a clear "via {provider}" attribution chip whenever a turn is answered by an external cloud LLM, and require an explicit confirm before the first cloud hand-off (Apple "Ask ChatGPT" model). (2) If we ever generate media, stamp it with C2PA + SynthID-style watermarking.

### 5.5 Cross-cutting privacy takeaways

1. **On-device-by-default flips every default:** make cloud routing, cloud training, and cloud retention all **opt-in** — the inverse of the big providers, and a genuine differentiator.
2. Two indicator layers (live capture + per-turn "where it ran").
3. A retention picker beats a binary toggle (copy Gemini's 3/18/36/never).
4. Ship an exportable Privacy Report (`.json`, time-windowed).
5. Be honest about safety-retention windows (OpenAI 30d, Gemini 72h) — say so rather than over-promising "nothing is stored."

**Sources (Area 5):** help.openai.com/en/articles/8914046-temporary-chat-faq · /8983778-chat-and-file-retention-policies-in-chatgpt · /7730893-data-controls-faq · /8912793-c2pa-in-dall-e-3 · /9737562-how-your-data-is-handled-when-you-use-chatgpt-through-apples-integrations · support.google.com/gemini/answer/13278892 · /answer/16722517 · blog.google/innovation-and-ai/products/ai-image-verification-gemini-app/ · deepmind.google/models/synthid/ · support.apple.com/en-us/108331 · source.android.com/docs/core/permissions/privacy-indicators · apple.com/legal/privacy/data/en/intelligence-engine/ · apple.com/legal/privacy/data/en/ask-siri-dictation/ · anthropic.com/news/updates-to-our-consumer-terms · anonyome.com/knowledge-center/ai-privacy/claude-privacy/ (secondary, for Claude incognito claim)
*(OpenAI Help pages returned 403 to direct fetch; claims rest on indexed snippets of the canonical OpenAI help articles. Claude ghost-icon detail is third-party — verify in-product.)*

---

## 6. On-Device → Cloud LLM Routing Techniques

The published literature splits into three families: **trained routers** (predict difficulty/win-rate before generating), **cascades** (generate cheap, escalate on low confidence), and **small-model-as-orchestrator** (the small model emits its own routing/tool token).

### 6.1 Router / classifier models — predict difficulty *before* generating

- **RouteLLM** (Ong et al., LMSYS/Berkeley + Anyscale, 2024, arxiv 2406.18665): trains a router on Chatbot Arena preference data to predict **P(win_strong | q)**; route to weak/local if `P < α`. **α is the single cost-quality knob.** Four router architectures (similarity-weighted, **matrix factorization = recommended**, BERT classifier, causal-LLM). **Reported: 95% of GPT-4 quality at 14% of GPT-4 calls (~85% cost cut); >2× cost reduction; routers transfer to unseen model pairs.**
- **Hybrid LLM** (Ding et al., Microsoft, ICLR 2024, arxiv 2404.14618): a **DeBERTa-v3-large (300M) binary router** predicts the quality gap `H(x)=q(small)−q(large)`; **runtime-tunable threshold** = the cost dial. **Probabilistic soft labels** (sample 10 responses/model) beat deterministic. **Reported: up to 40% fewer large-model calls at no quality drop.** Most directly portable blueprint.
- **Production routers:** NVIDIA LLM Router = DeBERTa task classifier (11 categories) + complexity classifier (6 dims). **katanemo/Arch-Router-1.5B** (arxiv 2506.16655) is a **Qwen2.5-1.5B router** keyed on **Domain × Action** — near-exact precedent for our on-device router (same model family).

**Adopt this:** Use the **matrix-factorization / BERT win-predictor with one tunable threshold α** for the *plain-chat* tier, trained on OUR traffic (not Arena). Steal **Arch-Router's Domain×Action policy schema** as our routing label space.

### 6.2 Confidence-threshold cascades — generate cheap, escalate on low confidence

- **FrugalGPT** (Chen/Zaharia/Zou, Stanford, 2023, arxiv 2305.05176): canonical cascade. A **DistilBERT regression scorer `g(q,a)→[0,1]`** judges the cheap answer; accept if it clears a per-model threshold, else escalate. **Reported: matches GPT-4 with up to 98% cost reduction.**
- **AutoMix** (Aggarwal/Madaan, CMU/Google, NeurIPS 2024, arxiv 2310.12963): small model **generates → self-verifies (context-grounded entailment, sampled k times) → routes** via a non-LLM **meta-verifier (POMDP)** weighing `Performance − λ·Cost` — and crucially **won't waste money escalating unsolvable queries.** >2× cost reduction.
- **The four escalation signals:** (a) **token log-probs / sequence likelihood** (Gupta & Jitkrittum, ICLR 2024 — beware length bias), (b) self-consistency voting (Wang, ICLR 2023), (c) trained verifier (Cobbe/OpenAI 2021; FrugalGPT's scorer), (d) cross-sample agreement (Yue & Zhao, ICLR 2024).
- **On-device confidence options:** max softmax probability (free, overconfident; Hendrycks 2017), semantic entropy (strongest unsupervised but multi-sample cost; Kuhn/Gal/Farquhar 2023), verbalized confidence ("how sure 0–100?"; Lin 2022 / Tian 2023). **CRITICAL for us:** "Confident or Seek Stronger" (2025, arxiv 2502.04428) benchmarks 8 UQ methods across 8 **small** models and finds **perplexity / log-probs and trained probes route better than verbalized confidence** — "are you sure?" *underperforms* on SLMs.

**Adopt this:** For the plain-chat fallback, let on-device Qwen answer, then escalate the uncertain turns using **token log-probs / perplexity — NOT "are you sure?"** (we own the weights, logits are free, and verbalized confidence is measurably worse on small models). Borrow AutoMix's meta-verifier idea: don't escalate queries the cloud also can't help with.

### 6.3 Small model as router / orchestrator — emit the routing decision as a token

Strongest fit for "the small model decides it needs a tool/internet → escalate."

- **Self-RAG** (Asai et al., ICLR 2024 Oral, arxiv 2310.11511): model emits **reflection tokens**; the first, **`Retrieve ∈ {yes,no,continue}`, IS the routing decision** ("can I answer from parametric knowledge, or look it up?"). Trained into the generator → **no extra model at inference.**
- **Adaptive-RAG** (Jeong et al., NAACL 2024, arxiv 2403.14403): a small T5-Large (770M) classifier routes to **3 tiers: (A) no retrieval / answer locally, (B) single-step retrieval, (C) multi-step iterative** — almost exactly our tier structure. **Labels are outcome-mined** (label each query by the simplest strategy that answered it correctly) — no manual annotation.
- **Toolformer** (Schick, NeurIPS 2023, arxiv 2302.04761): model learns *when* to emit a tool call by keeping only calls that reduce future-token loss. **FLARE** (arxiv 2305.06983): retrieve when the next predicted sentence has **low-probability tokens** (zero-cost backup trigger).
- **Speculative decoding — NOT a routing technique** (Leviathan, Google, arxiv 2211.17192; Chen, DeepMind, 2302.01318): small model drafts, big model verifies *every token*, output identical to the big model — a latency optimization that decides nothing about *where* work happens. **Do not build routing on it.**
- **Apple on-device + PCC** (arxiv 2407.21075): strong **architecture** precedent, **weak** router-signal citation — Apple does not publicly document its escalation logic.

**Adopt this:** Fine-tune on-device Qwen to **emit a `[ESCALATE]` / `[LOCAL]` control token as its first output** (Self-RAG style) — lowest-latency routing primitive, reuses the model we already run. Train it with **Adaptive-RAG's free outcome-mined labels** (log which turns local got right vs needed cloud). Use FLARE's low-token-probability as a zero-cost backup signal.

### 6.4 Practical routing-signals checklist

| # | Signal | Used by |
|---|--------|---------|
| a | Query length / token count | survey 2502.00409 |
| b | Complexity score from a classifier | NVIDIA classifier; Hybrid LLM |
| c | **Explicit tool / action need** (web search / calendar / send-message → cloud) | toolcall-verifier (HF); Arch-Router Action axis |
| d | **World-knowledge / recency** (fresh facts → internet) | Apple Intelligence; RouterBench |
| e | Router-predicted win-rate / quality gap | RouteLLM; RouterBench |
| f | On-device confidence / uncertainty (log-probs / perplexity / entropy) | FrugalGPT; "Confident or Seek Stronger" |
| g | Semantic similarity to known easy/hard queries | RouterBench KNN; RouteLLM sw_ranking |
| h | User-set preference (cost vs quality) | RouteLLM α; Arch-Router policy |
| i | Network availability / offline | Firebase AI Logic PREFER_ON_DEVICE |
| j | Battery / thermal / memory budget | EnerInfer; Edge survey |
| k | Domain / topic (code vs chitchat vs math) | Arch-Router Domain axis |
| l | Conversation context length | LAAR |

**Key findings for our design:** signals **(i) network** and **(j) battery/thermal** are edge-only and largely academic — the one productized cloud-fallback API (Firebase AI Logic) keys only on model availability, so **we must build connectivity + thermal/power logic ourselves.** **(d) recency/world-knowledge** is the cleanest justification for our cloud hop. **(c) explicit tool/action need** is under-covered in pure routing literature — our most important signal (actions live only in the cloud), so we lean on the small-model-as-orchestrator pattern, not a trained difficulty classifier.

### 6.5 Proposed Routing Algorithm (synthesized)

```
ON each user turn (after STT):

# ── STAGE 0: Hard gates (deterministic, pre-generation, ~0 cost) ──
if NOT network_available:            → LOCAL  (offline: Qwen-only, degrade gracefully)   # (i)
if battery_critical OR thermal_throttling: → LOCAL  (warn user cloud is paused)          # (j)
if PRIVATE_CHAT_MODE or PROCESS_ON_DEVICE_ONLY: → LOCAL  (never escalate; honor the toggle)

# ── STAGE 1: Does this NEED the cloud? (escalate-on-need) ──
# On-device Qwen emits a FIRST control token (Self-RAG / Toolformer style),
# trained on outcome-mined labels (Adaptive-RAG recipe).
route_token, domain, action = qwen.classify_turn(message, context)

if action in {WEB_SEARCH, SEND_MESSAGE, CALENDAR, MCP_TOOL, STATE_CHANGING}: → CLOUD  # (c) ALWAYS
if needs_recency_or_world_knowledge(route_token):                            → CLOUD  # (d)
if context_length > LOCAL_CTX_LIMIT (~4k tok):                              → CLOUD  # (l)

# ── STAGE 2: Plain-chat turn → try LOCAL, escalate on low confidence ──
answer, logprobs = qwen.generate(message, context)
confidence = mean_token_logprob(logprobs)   # perplexity, NOT "are you sure?" (per 2502.04428)  # (f)
threshold  = base_threshold − user_quality_preference + battery_saver_bias                       # (h)(j)
if confidence < threshold: → CLOUD  (FrugalGPT/Hybrid-LLM cascade)
else:                      → return LOCAL answer
```

**Why this ordering:** (1) hard device + privacy-toggle gates first — no point generating/escalating if we can't or shouldn't; (2) **"needs a tool/action/internet" is a hard escalate** — the load-bearing rule, since state-changing actions and web search ONLY exist in the cloud, so the local model must never fake them; (3) plain chat falls to a confidence-gated cascade using free token log-probs; (4) one tunable threshold slid by user cost-quality preference + battery.

**Implementation notes:** Train the router for free with Adaptive-RAG's outcome-mining (log every turn's outcome, mine labels, self-improve on real traffic). Two viable implementations — (1) a tiny separate classifier (Hybrid-LLM / Arch-Router), or (2) **fine-tune the on-device Qwen to emit the route token itself (Self-RAG style — elegant, no second model).** Given we already run Qwen, (2) is preferred; (1) is the safe fallback. Speculative decoding is irrelevant.

**Sources (Area 6):** arxiv.org/abs/2406.18665 (RouteLLM) · lmsys.org/blog/2024-07-01-routellm/ · github.com/lm-sys/RouteLLM · arxiv.org/abs/2404.14618 (Hybrid LLM) · /abs/2403.12031 (RouterBench) · developer.nvidia.com/blog/deploying-the-nvidia-ai-blueprint-for-cost-efficient-llm-routing/ · huggingface.co/nvidia/prompt-task-and-complexity-classifier · huggingface.co/katanemo/Arch-Router-1.5B · arxiv.org/abs/2506.16655 · /abs/2305.05176 (FrugalGPT) · /abs/2310.12963 (AutoMix) · /abs/2404.10136 · /abs/2203.11171 · /abs/2110.14168 · /abs/2310.03094 · /abs/1610.02136 · /abs/2302.09664 · /abs/2205.14334 · /abs/2305.14975 · /html/2502.04428 (Confident or Seek Stronger) · /abs/2310.11511 (Self-RAG) · /abs/2403.14403 (Adaptive-RAG) · /abs/2302.04761 (Toolformer) · /abs/2305.06983 (FLARE) · huggingface.co/llm-semantic-router/toolcall-verifier · /abs/2211.17192 + /abs/2302.01318 (spec decoding, contrast) · /abs/2407.21075 (Apple) · firebase.google.com/docs/ai-logic/hybrid-on-device-inference
*(Caveat: Apple's escalation logic is undocumented; a few fetched arxiv IDs carry 2026 dates — load-bearing citations RouteLLM/FrugalGPT/Hybrid-LLM/AutoMix/Self-RAG/Adaptive-RAG are established 2023–2025 work.)*

---

## Top 8 Patterns to Adopt for LazyClaw "Hey Lazy"

1. **Escalate-on-NEED, then confidence-gate the rest (the routing core).** Two-stage router: STAGE 1 hard-escalates any turn needing a tool / internet / state-change / fresh world-knowledge / >~4k-token context (these live ONLY in the cloud — the local model must never fake them); STAGE 2 lets local Qwen answer plain chat and escalates only low-confidence turns via **token log-probs / perplexity** (NOT verbalized "are you sure?", which is measurably worse on small models — arxiv 2502.04428). *Source: Self-RAG + Adaptive-RAG + FrugalGPT/Hybrid-LLM + RouteLLM.*

2. **The on-device model emits its OWN route token.** Fine-tune Qwen to output `[LOCAL]` / `[ESCALATE]` (+ domain × action) as its first token, trained on **free outcome-mined labels** (log which turns local got right vs needed cloud) so it self-improves on real traffic — no second model, lowest latency. *Source: Self-RAG `Retrieve` token, Adaptive-RAG labeling, Arch-Router Domain×Action (itself a Qwen-1.5B).*

3. **Copy `InferenceMode` + a `FeatureStatus` availability gate.** A 4-value routing enum (`preferOnDevice / preferCloud / onlyOnDevice / onlyCloud`, default preferOnDevice) plus an availability lifecycle (`unavailable / downloadable / downloading / available`) checked before showing on-device UI. *Source: Google Firebase AI Logic + ML Kit GenAI.*

4. **One global "Process data only on device" master switch, default OFF, with graceful per-action degradation.** Single toggle at the top of settings; tag every intent `onDevice | cloudRequired`; when ON, keep local intents live and only gray out cloud ones — never kill the assistant or throw errors. Show an inline live "stays on-device / becomes unavailable" list right under the toggle. *Source: Samsung Galaxy AI.*

5. **Risk-tiered voice-action confirmation (consequence + reversibility, not action type).** Declarative `confirm: explicit | implicit` per action: **`send_message` + any delete → explicit confirm**; **`add_task` / `add_expense` / `set_budget` → implicit read-back only.** Confirm sparingly. *Source: Apple HIG + Amazon Alexa + Google — independent convergence.*

6. **Always read back one line + repair-at-read-back + 1-turn undo.** Every state change speaks verb + key slot ("Logged €12 to groceries"); accept an immediate verbal correction and re-fire; keep the new DB row id for ~one turn to honor "undo that." Suppress the spoken line when the screen already shows it. *Source: Apple `UndoableIntent` + Amazon/Google implicit confirmation.*

7. **Two-layer transparency: live capture indicator + per-turn "where it ran" badge, plus an exportable Privacy Report.** A live mic indicator (color + shape, accessibility-safe) during capture, an **On-device (green) / Cloud (amber)** pill per turn, and a time-windowed `.json`-exportable log of which turns left the device and to which provider. This is where we BEAT Apple, whose PCC escalation is silent. *Source: iOS/Android sensor dots + Apple Intelligence Report.*

8. **Non-bypassable consent gate for any data leaving the device — strongest for attachments — + on-device-first defaults.** Per-request consent for the external/cloud path with an "Ask Cloud…" inline-consent phrase; ANY request carrying media/docs forces a SEPARATE confirmation that names the attachment and ignores "don't ask again." Default cloud training/retention to OFF (opt-in), offer a Gemini-style retention picker (30d / 6m / 18m / never) + delete-all, and a per-conversation Private mode where on-device-only turns leave nothing behind. Show a "via {provider}" attribution chip on external answers. Be honest about safety-retention windows. *Source: Apple ChatGPT consent + attachment gate; Gemini retention; OpenAI Temporary Chat.*
