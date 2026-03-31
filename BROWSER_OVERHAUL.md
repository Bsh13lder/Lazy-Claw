# ADR-005: Browser Agent Intelligence Overhaul

**Status:** Proposed
**Date:** 2026-03-31
**Deciders:** BLCK

---

## Context

The LazyClaw browser system has solid infrastructure (CDP backend, ref-ID snapshots, stealth, site memory, stuck detector, remote takeover) but the *intelligence layer* — how the agent **thinks** about browsing — is weak. The agent fires actions one-by-one without planning, doesn't verify outcomes, gets stuck on trivial navigation, and the fallback/recovery path is clunky.

**Root causes identified** (from codebase audit + online research of Browser Use, Anthropic Computer Use, Vercel agent-browser):

### Problem 1: No Action Planning (Fire-and-Forget)

**Where:** `agent.py` agent loop + `browser_skill.py` execute()

The agent receives a task like "research X on the web" and immediately starts calling browser actions without any plan. Each action is independent — there's no concept of "I need to: 1) search, 2) open top 3 results, 3) extract key info, 4) synthesize." The LLM just guesses the next action each turn.

**What best agents do:** Decompose task into stages with checkpoints. Plan 3-5 actions ahead. Identify fallback paths before hitting failures.

### Problem 2: No Verification After Actions (Blind Execution)

**Where:** `browser_skill.py` click/type/open handlers

After clicking or typing, the skill checks `is_stale()` and returns a fresh snapshot if the page changed. But it never *verifies the action achieved its goal*. Did the click actually navigate? Did the form submit? Did an error modal appear? The agent doesn't know — it just gets raw refs back and guesses.

**What best agents do:** Before/after snapshot diff. Semantic verification ("did the URL change?", "did the expected element appear?", "is there an error message?"). Each action has a success/fail signal, not just "I clicked something."

### Problem 3: Stuck Detection Too Late + Recovery Too Dumb

**Where:** `stuck_detector.py` + `agent.py` lines 1438-1565

The stuck detector requires 3+ identical results or 8 consecutive browser calls before triggering. By then the agent has burned tokens and time. When it does trigger, the recovery is a blunt system message saying "STOP repeating" with 4 options. The LLM often ignores this or picks poorly. The escalation to visible browser/noVNC is useful but arrives too late.

**What best agents do:** Detect after 2 failed attempts (not 8). Each retry must be *materially different* (enforced, not suggested). Escalation ladder: code-level retry → strategy shift → different model → human help. Max 2 strategy shifts before escalating.

### Problem 4: No Page Understanding Before Acting

**Where:** `browser_skill.py` — agent often clicks without reading

The agent frequently tries to click elements without first understanding what page it's on. It gets a snapshot with ref-IDs but doesn't comprehend the page context. On a search results page, it should know "I see 10 results, the top 3 are relevant." Instead it just sees `[e1] link "Result Title"` and clicks blindly.

**What best agents do:** Always `read` before `act`. Build a mental model of the page: what type of page is this? What are the key sections? What's the goal on this page? Then act with purpose.

### Problem 5: No Research Strategy for Web Tasks

**Where:** `agent.py` tool selection + browser_skill interaction

When asked to "research topic X", the agent has no research methodology. It opens one page, reads it, maybe searches again, but there's no structured approach: define what info is needed → search → evaluate sources → extract → cross-reference → synthesize. It also doesn't know when it has *enough* information to stop.

**What best agents do:** Define information requirements upfront. Use web_search for discovery, browser for deep reading. Track what's been found vs. what's still needed. Know when to stop (diminishing returns).

---

## Decision

Overhaul in 6 focused sessions, each independently testable. No rewrites — surgical additions to existing code.

---

## Session Prompts (Copy-Paste to Claude Code)

### Session 1: Browser Action Planner

**Files:** `lazyclaw/runtime/agent.py`, new file `lazyclaw/browser/action_planner.py`

**Task:** Create a lightweight action planner that sits between the agent loop and browser_skill. When the agent decides to use the browser, the planner intercepts and:

1. Creates a `BrowsingPlan` dataclass with: goal (string), steps (list of planned actions), current_step (int), verification_criteria (what success looks like for each step), fallback_actions (what to try if step fails)
2. Before the first browser call on a new page/task, inject a system message asking the LLM to output a brief plan: "What are the 3-5 steps to accomplish this? What does success look like at each step?"
3. Store the plan in the agent's context (not DB — ephemeral per-conversation)
4. After each browser action, check: did this step succeed per the criteria? If not, try the fallback. If fallback fails, replan.

The planner should be a pure Python module with no side effects. It takes the current plan + action result and returns: (next_action, updated_plan, status). Status is one of: CONTINUE, REPLAN, ESCALATE.

Key constraint: This is NOT a separate LLM call. It's a system message injection that makes the existing brain LLM call output a plan as structured JSON before acting. One LLM call, plan + first action together.

Read `lazyclaw/runtime/agent.py` (especially the tool dispatch loop around line 1380+) and `lazyclaw/browser/snapshot.py` to understand the current flow. The planner hooks in at the point where `detect_stuck` currently runs — but it runs BEFORE actions, not after failure.

---

### Session 2: Action Verification Layer

**Files:** `lazyclaw/browser/action_verifier.py` (new), `lazyclaw/skills/builtin/browser_skill.py`

**Task:** Add post-action verification to every browser action (click, type, open, press_key). Currently browser_skill just checks `is_stale()` and returns refs. We need semantic verification.

Create `ActionVerifier` class with method `verify(before_state, after_state, intended_action) -> VerificationResult`. VerificationResult has: succeeded (bool), evidence (string — what changed), suggestion (string — what to try if failed).

State comparison logic (NO LLM call — pure Python):
- URL changed? (for navigation actions)
- Page title changed? (for navigation)
- Target element disappeared? (for click — means it navigated away or closed a modal)
- New error elements appeared? (check for role="alert", class*="error", text containing "error", "failed", "invalid")
- Form field value changed? (for type actions — check the ref's value after typing)
- Element count in main landmark changed? (for actions that should add/remove items)
- Page content hash changed? (reuse the watcher hash logic from `browser/watcher.py`)

Wire into `browser_skill.py`: before each action, capture minimal state (url, title, target element ref, main element count). After action, capture again. Run verifier. Append verification result to the tool response string so the LLM sees "Clicked [e5] → SUCCESS: page navigated to /inbox" or "Clicked [e5] → FAILED: page unchanged, no error visible. Try: take snapshot to check if element was correct."

Read `browser_skill.py` (the _action_click, _action_type, _action_open methods) and `browser/snapshot.py` (is_stale, take_snapshot). Also read `browser/watcher.py` for the hash comparison pattern.

---

### Session 3: Smarter Stuck Detection + Graduated Recovery

**Files:** `lazyclaw/runtime/stuck_detector.py`, `lazyclaw/runtime/agent.py`

**Task:** The current stuck detector waits too long (8 browser calls, 3 identical results) and the recovery is a blunt "STOP repeating" message. Fix both.

**Stuck detector changes:**
1. Add a new detector: `detect_no_progress`. Track the verification results from Session 2. If last 2 actions both returned FAILED verification, that's stuck — don't wait for 8 calls.
2. Lower `browser` loop limit from 8 to 5 (still higher than default 3, but 8 was way too generous).
3. Add `detect_hallucinated_element`: if the agent tries to click a ref that doesn't exist 2+ times, it's hallucinating page state. Force a fresh snapshot.

**Graduated recovery (replace the current blunt escalation in agent.py ~line 1462):**

Instead of one "STOP repeating" message, implement a 3-level escalation:

Level 1 (soft — after 2 failed verifications): Inject system message: "Your last 2 browser actions didn't achieve their goal. Before your next action: 1) Use action='read' to understand the current page. 2) Use action='snapshot' to see available elements. Then try a DIFFERENT approach."

Level 2 (medium — after Level 1 fails, i.e. 2 more failed actions): Switch to brain model (already exists). Inject stronger message: "STRATEGY CHANGE REQUIRED. Describe what you've tried, why it failed, and propose a completely different approach. If the page requires login or human interaction, say so."

Level 3 (hard — after Level 2 fails): Current behavior — escalate to user with HELP_NEEDED event + visible browser option. But now it arrives after 6 failed actions instead of 8+ identical ones.

Read `stuck_detector.py` (full file, it's small) and `agent.py` lines 1430-1570 (the stuck handling block). The graduated levels replace the single escalation block.

---

### Session 4: Page Understanding Before Acting

**Files:** `lazyclaw/skills/builtin/browser_skill.py`, `lazyclaw/browser/page_reader.py`

**Task:** The agent often acts on a page without understanding it. Add a `_page_context_summary` enhancement that gives the LLM actual comprehension, not just raw refs.

**Enhance `_page_context_summary` (already exists around line 518):**

After getting the JS extractor content + snapshot, add a structured header:

```
📍 Page: {title}
🔗 URL: {url}
📄 Type: {page_type} (search_results | article | form | app | login | error | other)
🎯 Key sections: {landmark summary — "main: 15 elements, navigation: 8 elements, form: 3 elements"}
⚠️ Alerts: {any role="alert" or error-class elements — "None" or "Error: Invalid password"}
```

**Add page type detection enhancement to `page_reader.py`:**

The `_detect_page_type` function already exists but returns basic types. Enhance it to detect:
- `search_results` — has multiple result-like links in main content
- `login` — has password field + submit button
- `error` — has error/alert elements or HTTP error in title
- `form` — has 3+ input fields
- `list` — has repeated similar elements (product list, email list, etc.)
- `confirmation` — has "success", "confirmed", "thank you" patterns

This detection is pure JS (run via CDP evaluate) — no LLM cost. Return the type as part of the extractor result so browser_skill can format it.

**Add to `open` action response:** Always include the structured page context header. Currently `_action_open` returns raw content + refs. Prefix with the structured header so the LLM immediately knows what kind of page it landed on.

Read `browser_skill.py` method `_page_context_summary` (~line 518) and `page_reader.py` functions `_detect_page_type` and `run_extractor`.

---

### Session 5: Research Strategy System

**Files:** new file `lazyclaw/browser/research_strategy.py`, `lazyclaw/runtime/agent.py`

**Task:** When the user asks to "research X" or "find information about Y", the agent currently has no methodology. Create a lightweight research orchestrator.

**Create `ResearchStrategy` dataclass:**
- query: str (the research question)
- info_requirements: list[str] (what specific info is needed — populated by LLM on first turn)
- sources_checked: list[dict] (url, title, relevant_info extracted, quality score 1-5)
- gaps: list[str] (what info is still missing)
- status: GATHERING | SUFFICIENT | EXHAUSTED

**Integration point in agent.py:**

Detect research-like tasks (keywords: "research", "find out", "look up", "what is", "compare", "analyze" + topic). When detected, inject a system message asking the LLM to fill info_requirements first:

"You're researching: {query}. Before browsing, list 3-5 specific pieces of information you need to find. Format as JSON array of strings."

After each browser read/search action, update sources_checked. After each update, inject a progress message:

"Research progress: Found {n}/{total} requirements. Still need: {gaps}. Sources checked: {count}. {CONTINUE searching | You have ENOUGH info to answer | EXHAUSTED reasonable sources — synthesize what you have}."

The strategy tracks when to STOP — this prevents the endless browsing loop. If 3+ sources checked and all requirements met → stop. If 5+ sources checked and requirements still unmet → synthesize what's available.

This is NOT a separate LLM call. It's context injection that guides the existing brain LLM.

Read `agent.py` (the message building section and tool dispatch loop) to understand where to inject the research context. Also read `browser/site_memory.py` to see how per-domain knowledge is stored — research results could leverage this.

---

### Session 6: User Transparency (Show What's Happening)

**Files:** `lazyclaw/runtime/agent.py`, `lazyclaw/runtime/callbacks.py`

**Task:** The user currently sees nothing while the browser agent works — then either gets a result or a "stuck" message. Add step-by-step transparency.

**New AgentEvent types in callbacks.py:**
- `BROWSER_PLAN`: emitted when action planner creates/updates a plan. Payload: {goal, steps, current_step}
- `BROWSER_ACTION`: emitted before each browser action. Payload: {action, target, step_number, total_steps}
- `BROWSER_VERIFY`: emitted after verification. Payload: {action, succeeded, evidence}
- `BROWSER_PROGRESS`: emitted during research. Payload: {sources_checked, requirements_met, gaps}

**Wire into agent.py:** After each browser tool call, emit the appropriate event via `cb.on_event()`. The Telegram channel adapter already handles AgentEvent — it just needs to format these new types into user-friendly messages.

**Telegram formatting (in the Telegram channel adapter):**
- BROWSER_PLAN → "🎯 Plan: {goal}\n1. {step1}\n2. {step2}..."
- BROWSER_ACTION → "⏳ Step {n}/{total}: {action} on {target}..."
- BROWSER_VERIFY → "✅ {evidence}" or "❌ {evidence} — trying alternative..."
- BROWSER_PROGRESS → "📊 Research: {met}/{total} found. Still looking for: {gaps}"

Keep it simple — these are one-line status messages, not full reports. The user just needs to know the agent is working and what it's doing.

Read `runtime/callbacks.py` (AgentEvent class and the callback interface) and `channels/telegram_adapter.py` (how events are formatted and sent). The new events use the same pattern as existing HELP_NEEDED/HELP_RESPONSE events.

---

## Implementation Order & Dependencies

```
Session 1 (Planner) ─── standalone, no deps
Session 2 (Verifier) ── standalone, no deps
Session 3 (Stuck) ───── depends on Session 2 (uses verification results)
Session 4 (Page Understanding) ── standalone, no deps
Session 5 (Research) ── benefits from Sessions 1+2+4 but can work alone
Session 6 (Transparency) ── depends on Sessions 1+2+5 (emits their events)
```

**Recommended order:** 1 → 2 → 4 → 3 → 5 → 6

Sessions 1, 2, and 4 can run in parallel (no dependencies). Session 3 needs Session 2's verification results. Session 5 benefits from all prior sessions. Session 6 wires everything to the user.

---

## Consequences

**What becomes easier:**
- Agent plans before acting → fewer wasted browser calls
- Verification catches failures immediately → faster recovery
- Graduated escalation → user only bothered when truly needed
- Page understanding → agent knows what it's looking at
- Research strategy → structured info gathering instead of random browsing
- Transparency → user trusts the agent, can intervene early

**What becomes harder:**
- More system message injection → slightly more tokens per turn (but saves tokens overall by reducing retries)
- Action planner adds complexity to the agent loop — must not break fast-path for simple messages

**What we'll need to revisit:**
- Token budget: the planner + verifier + research context add ~200-400 tokens per browser turn. Monitor with ECO mode to ensure costs stay low.
- Stuck thresholds: the new numbers (2 failed verifications, 5 loop limit) need tuning based on real usage.
- Site memory integration: research results could feed into site_memory for future tasks — deferred to a later phase.

---

## Test Plan (Per Session)

| Session | Test |
|---------|------|
| 1 | Ask "find the price of iPhone 16 on Amazon" — agent should output a plan before first click |
| 2 | Click a non-existent element — verifier should return FAILED with suggestion |
| 3 | Navigate a page that requires login — should escalate at Level 2, not after 8 calls |
| 4 | Open google.com — response should say "Type: search, Key sections: form (1 search input)" |
| 5 | Ask "research the best Python web frameworks in 2026" — should list info requirements, track progress, stop when enough |
| 6 | Run any browser task — Telegram should show step-by-step status messages |
