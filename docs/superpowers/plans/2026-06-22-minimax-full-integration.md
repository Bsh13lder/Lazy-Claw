# MiniMax Full Integration — text (M2.5/M2.7/M3) + vision + generative suite

**Date:** 2026-06-22 · **REVISED 2026-06-23** (branch `feat/minimax-anthropic-integration`)
**Status:** REVISED — **keep the Anthropic-compatible endpoint** (`api.minimax.io/anthropic`). The original "switch text transport to OpenAI-compat" thesis (old Design Decision #1 / old Phase 1) is **DROPPED** — it was solving a non-problem.

**Why the reversal (evidence, not assumption):**
- **The idealista incident (2026-06-23, ~9–12 turns, never executed) was 100% lazyclaw-side, not an endpoint defect.** Root causes: the brain was handed **only meta/read-only tools** (no `browser`/`create_sheet`) for property-search / "create sheet" prompts; M3's one *real* `use_host_browser` call was **dropped** by the channel-suppression guard; `tool_choice="auto"` on iter0 + a polluted "I have no tools" history fed a narration loop.
- **Live probe through lazyclaw's own `AnthropicProvider`** (2026-06-23): M3 AND M2.7 emit `tool_use` **reliably** on `api.minimax.io/anthropic` across `tool_choice` = auto / any / forced (5/5 scenarios returned a real tool call). The endpoint is NOT flaky for tool calling.
- **Official docs confirm** the Anthropic endpoint is a **first-class, recommended drop-in** ([MiniMax Anthropic SDK docs](https://platform.minimax.io/docs/api-reference/text-anthropic-api)) supporting M3/M2.7/M2.5, **and M3 vision (image+video content blocks) works on the same endpoint** — so vision needs no separate provider.
- Known gotcha: the `/anthropic` endpoint can report **200K ctx instead of 1M** ([Issue #46](https://github.com/MiniMax-AI/MiniMax-M2.7/issues/46)) → pin `max_context` realistically. M3 thinking via `thinking:{"type":"adaptive"}` (off by default).

So the fix is: **fix the lazyclaw-side bugs, keep M3 on the Anthropic-compat endpoint, ride the same endpoint for vision, finish the generative `/v1` REST skills.** Original research below retained for reference; superseded items marked.

---

## Research summary (June 2026, all from official `platform.minimax.io` docs)

### Text / LLM
- **Models (exact API ids):** `MiniMax-M3` (1M ctx, **multimodal: image+video in**, flagship brain), `MiniMax-M2.7` (204K, text, top coding/agentic), `MiniMax-M2.5` (204K, text, worker), `-highspeed` variants (2× output price, faster), `MiniMax-M2.1`/`MiniMax-M2` (legacy).
- **Surface:** OpenAI-compatible `POST https://api.minimax.io/v1/chat/completions` (drop-in for the OpenAI SDK via `base_url`). Auth `Authorization: Bearer <key>`; **no GroupId** for chat.
- **Tool calling:** OpenAI-style `tools` / `tool_calls`; return results as `{"role":"tool","tool_call_id":…,"content":…}`. **Load-bearing quirk:** must replay the **full assistant message including reasoning** (`reasoning_content` / `reasoning_details`, via `extra_body={"reasoning_split":true}`) every turn or quality degrades. Parallel tool calls supported.
- **Vision:** `MiniMax-M3` only; OpenAI content blocks `image_url`/`video_url` (https URL or base64 data-URL). Image ≤10 MB; video ≤50 MB inline.
- **Error envelope:** check `base_resp.status_code == 0` **even on HTTP 200** (e.g. `1008` insufficient balance, `1004` not authorized, `2049` invalid key).
- **Pricing (PAYG):** M3/M2.7 `$0.30/$1.20` per M (≤512K); **M3 doubles >512K input** → `$0.60/$2.40`. JSON-schema `response_format` only on legacy `MiniMax-Text-01` → on M3/M2.x prompt-instruct + client-validate.
- **Token Plan (flat sub):** Plus $20 / Max $50 / Ultra $120; **different key from PAYG, same base_url** (swap the key only); shared multimodal quota (text+image+speech+music); 5-hour + weekly windows, no rollover. Rate limits: M3 200 RPM, M2.x 500 RPM.

### Generative / multimodal (each has a PAYG price; text/image/speech/music share the flat Token Plan quota; video tier-conditional)
| Capability | Model id | Endpoint | Sync? | Price |
|---|---|---|---|---|
| Image gen | `image-01` | `POST /v1/image_generation` | sync | $0.0035/image |
| TTS / voice | `speech-2.8-hd` / `-turbo` | `POST /v1/t2a_v2` (+ async for ≤1M chars) | sync | $100 / $60 per M chars; clone $1.50 |
| Music | `music-2.6` | `POST /v1/music_generation` | sync | $0.15/track |
| Video | `MiniMax-Hailuo-2.3` | `POST /v1/video_generation` → poll → retrieve | **async** | ~$0.22–$0.53/clip |
| Embeddings | `embo-01` ⚠️ legacy/undocumented (GroupId required) | `POST /v1/embeddings` | sync | ~$0.07/M (CNY-derived) |

---

## Current state (codebase audit)
- **Providers:** `anthropic_provider.py`, `claude_cli_provider.py`, `claude_sdk_provider.py`, `mlx_provider.py`, `ollama_provider.py`, `openai_provider.py`. Base ABC `llm/providers/base.py`: `chat()`, `verify_key()`, `stream_chat()`.
- **MINIMAX mode TODAY** (`model_registry.py:53-60`): `brain=worker=MiniMax-M2.7`, `fallback=claude-haiku-4-5` — routed through the **Anthropic-compatible** endpoint `api.minimax.io/anthropic` via `anthropic_provider.py`. So **structured tool I/O already works here** (this is why MINIMAX grounds cleaner than CLAUDE/SDK). Gaps: only M2.7, no M3, no vision, fallback depends on the **dead** Anthropic key ($0 balance).
- **Pricing** (`pricing.py:23-26`): minimax entries hardcoded `$0.0` (Token-Plan $0 marginal) — fine, but should reflect PAYG for accounting.
- **Vision today:** `llm/vision_query.py` + `browser/apple_vision.py` (Apple Vision OCR, ~200ms native) + `ask_vision`/`ocr` browser actions. Local VLM path retired (memory `vision_delegation`). No cloud VLM.
- **No image-gen, no TTS skills** exist. **Embeddings:** local Ollama `nomic-embed-text` (768-d, $0, private) in `lazybrain/embeddings.py`.
- **Key:** `.env` has `MINIMAX_API_KEY` set.

---

## Design decisions (recommendations — confirm before build)

1. ~~**Text transport → switch to OpenAI-compatible**~~ **DROPPED (2026-06-23, live-verified).** Keep the **Anthropic-compatible** endpoint via the existing `AnthropicProvider`. M3 emits `tool_use` reliably there (probe: 5/5), it is MiniMax's recommended drop-in, and it carries M3 vision blocks too. No new `minimax_provider.py`. The real fixes are lazyclaw-side tool-injection + suppression + prompt deploy (new Phase 1 below).
2. **All-MiniMax role mapping** (no dependency on the dead Anthropic key): `brain=MiniMax-M3`, `worker=MiniMax-M2.7`, `fallback=MiniMax-M2.5`. Tunable in Settings.
3. **Vision → M3** powers a cloud image/video understanding path (augments, not replaces, Apple Vision OCR which stays for fast local text).
4. **Generative skills scope:** ship **image gen + TTS** now (high value, in the flat quota); **music + video** as fast-follow (video is async + tier-conditional billing); **embeddings: keep Ollama** (MiniMax `embo-01` is legacy/undocumented/risky — skip, revisit only if needed).
5. **Token Plan:** set the subscription key as `MINIMAX_API_KEY` (different from PAYG; same base_url). Start on **Plus $20**, bump to Max if quota-limited. Image/TTS/music draw from the same quota (no surprise bills); video may need a separate pack.
6. **CONFIRMED (2026-06-22): embeddings stay on our Ollama `nomic-embed-text`** — no MiniMax embeddings. Phase 4 dropped.
7. **CONFIRMED (2026-06-22): code work stays on Claude.** Code goals / the code specialist must continue routing through the **Claude Code MCP** (`mcp__claude-code__*`, persistent session — memory `feedback_code_tasks_via_claude_code_mcp`), so code tasks keep Claude quality **regardless of ECO mode = MINIMAX**. New gate: verify switching ECO→MINIMAX does NOT redirect code-tagged goals to MiniMax; pin the code path to Claude Code MCP.

---

## Phased plan (checkable)

### Phase 1 — Idealista bug: make MINIMAX actually act (the real agent fix) ⭐ [REVISED 2026-06-23]
The endpoint is fine; the brain never got usable tools and its one real call was dropped. Fix that:
- [ ] **Tool injection gates** (`runtime/agent.py`): property/rental/listing intents (idealista, rightmove, zillow, "find … apartments/rentals", price-on-site) inject the native `browser` skill; "create sheet"/"local sheet"/"save CSV to a sheet" inject native `create_sheet`/`set_cells` (NOT only the Google path). Add a catch-all: when the brain has a clear domain intent but no matching gate, ensure `delegate` is present and the prompt steers to it.
- [ ] **Suppression guard** (`runtime/agent.py` ~3243): stop dropping a legitimate `browser`/`use_host_browser` tool call when the user explicitly asked for browser work this turn. The "MiniMax resurrects browser from training memory" defense must not kill the user's actual request.
- [ ] **Deploy the personality blocks** already written but never shipped: `[ROUTER — DELEGATE DOMAIN WORK]` + `[NEVER REFUSE]` in `_MINIMAX_TOOL_DISCIPLINE_SUFFIX` (`runtime/personality.py`). (Server runs a baked image → needs `make rebuild` to go live.)
- [ ] **History pollution**: quarantine capability-denial narration ("I literally only have N tools", "I don't have access", "you open … in your browser") from assistant history so it can't seed the next turn's refusal loop (mirror `context_journal_filter` wikilink quarantine).
- [ ] **Context window**: pin `MiniMax-M3.max_context` to the value the `/anthropic` endpoint actually advertises (200K per Issue #46), not the nominal 1M, so the context builder doesn't over-pack.
- [ ] Keep `MODE_MODELS["minimax"]` = `brain=M3, worker=M2.7, fallback=M2.5` (M3 does tools AND vision; live-verified). Keep `pricing.py` $0 (Token Plan).
- [ ] **Tests:** tool-injection gate unit tests (idealista→browser, "local sheet"→create_sheet); suppression-guard keeps explicit browser request; personality suffix contains the new blocks; capability-denial quarantine.
- [ ] **Verify:** real end-to-end Idealista-style turn in MINIMAX mode proving the brain emits a real `browser`/`delegate` call (tool_calls>0) and does NOT narrate-refuse.

### Phase 2 — Vision (M3 multimodal)
- [ ] Route `vision_query.py` / `ask_vision` to M3 for image+video understanding (cloud VLM), Apple Vision OCR stays for fast local text.
- [ ] Tests + a real image-understanding call.

### Phase 3 — Generative skills (new builtins)
- [ ] `generate_image` (`image-01`) — sync, returns url/base64, delivers via `push_telegram_document` + web download.
- [ ] `text_to_speech` (`speech-2.8`) — voice reply; optional voice-clone. Pairs with existing STT.
- [ ] Permissions categories + registry entries; tests.
- [ ] (Fast-follow) `generate_music` (`music-2.6`), `generate_video` (`Hailuo-2.3`, async poll).

### Phase 4 — Optional / deferred
- [ ] Embeddings: keep Ollama; only add `embo-01` behind a flag if a cloud option is needed (legacy/undocumented risk).

---

## Risks / smoke-test before relying (from research [L]/[M] flags)
- `tool_choice:"required"` / forced-named function — undocumented; test.
- Streaming tool-call deltas — undocumented + known serving bugs; accumulate by index, keep non-stream fallback.
- `response_format` schema unsupported on M3/M2.x — use prompt-instruct + validate.
- Reasoning-replay quirk — if not replayed, agentic quality drops (must thread `reasoning_content` through lazyclaw's message history).
- M3 >512K input doubles price — guard long-context turns.
- Token Plan key ≠ PAYG key; video may bill separately from the flat quota.

## Test/verification gate
Each phase ships only after: unit tests green + one real live call proving the capability + (Phase 1) a head-to-head vs the SDK path with measured success-rate/latency/cost so "better" is a number, not a claim.
