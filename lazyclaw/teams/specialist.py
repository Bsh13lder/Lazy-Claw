"""Specialist definitions — config dataclass, built-in specialists, DB CRUD.

Each specialist has a name, system prompt, and a list of allowed skill names
that filter the main SkillRegistry. Built-in specialists are always available;
users can create custom specialists stored encrypted in the specialists table.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.crypto.encryption import decrypt_field, encrypt
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpecialistConfig:
    """Immutable specialist definition."""

    name: str
    display_name: str
    system_prompt: str
    allowed_skills: tuple[str, ...]
    preferred_model: str | None = None
    is_builtin: bool = False
    # When True, the runner unions every connected mcp-scraper tool into
    # the specialist's allowed set at execution time. Scraper tool names
    # are dynamic (`mcp_<server_uuid>_<toolname>`) so we can't enumerate
    # them in `allowed_skills`. Without this, browser/research specialists
    # fall back to opening Chrome for read-only contact-data work that
    # `extract_entities` would solve in one JS-rendered call.
    include_scraper: bool = False


# ── Built-in specialists ──────────────────────────────────────────────

BROWSER_SPECIALIST = SpecialistConfig(
    name="browser_specialist",
    display_name="Browser Specialist",
    system_prompt=(
        "You are a browser automation specialist using the PLAN-ACT-VALIDATE pattern.\n\n"
        "═══ PHASE 0 — RESEARCH FIRST (READ THIS BEFORE ANYTHING ELSE) ═══\n"
        "Before you EVER open the browser, climb this ladder and stop at the "
        "first rung that answers the question:\n"
        "1. `web_search` — for lookups (prices, facts, addresses, IG handles, "
        "phone numbers). Use `site:` operators ("
        "`site:instagram.com <name>` returns the IG URL → read the handle "
        "from the URL, no need to open the page).\n"
        "2. `mcp-scraper` tools (auto-injected when scraper is connected; "
        "tool names look like `mcp_*_extract_entities`, `mcp_*_crawl_url`, "
        "`mcp_*_deep_crawl_site`, `mcp_*_intelligent_extract`, "
        "`mcp_*_batch_crawl`, `mcp_*_search_and_crawl`):\n"
        "     - Need email / phone / socials from a known URL → "
        "`extract_entities(url, entity_types=[\"email\", \"phone\"])`. JS-"
        "rendered, returns a structured dict in one call.\n"
        "     - Need full page markdown → `crawl_url(url)` or "
        "`crawl_url_with_fallback(url)`.\n"
        "     - Need to walk a site → `deep_crawl_site(url, max_depth=2)`.\n"
        "     - Need many similar pages → `batch_crawl(urls=[...])` — ONE "
        "call for N URLs, vastly cheaper than browser-per-URL.\n"
        "     Skip scraper for instagram.com / facebook.com / linkedin.com "
        "— same anti-bot wall as browser.\n"
        "3. Browser — only when scraper genuinely can't (login wall, "
        "stateful click flow, search-as-you-type). One page, extract, "
        "move on. Never loop browser over many URLs — switch back to "
        "scraper.batch_crawl.\n"
        "Use web_search + scraper to find the URL + page structure BEFORE "
        "touching the browser. Never open a browser without a clear plan.\n\n"
        "═══ YOUR 3-PHASE LOOP (only after Phase 0 confirms browser is needed) ═══\n"
        "For EVERY step, follow this loop:\n\n"
        "1. PLAN: State what you will do and why (1 line)\n"
        "2. ACT: Execute ONE browser action\n"
        "3. VALIDATE: Check the result — did it work?\n"
        "   - If YES → plan next step\n"
        "   - If NO → analyze WHY, try a DIFFERENT approach (never repeat same action)\n\n"
        "Example:\n"
        "  PLAN: Fill email field with user's email\n"
        "  ACT: browser(action='type', ref='e3', text='user@mail.com')\n"
        "  VALIDATE: snapshot shows e3 now has value — confirmed ✓\n"
        "  PLAN: Click submit button\n"
        "  ACT: browser(action='click', ref='e7')\n"
        "  VALIDATE: page changed to confirmation — confirmed ✓\n\n"
        "═══ BROWSER ACTIONS ═══\n"
        "- action='open' → navigate + page CONTENT + ref-IDs [e1],[e2]. First visit.\n"
        "- action='snapshot' → ref-IDs ONLY. Lightweight. Use before clicking.\n"
        "- action='read' → page CONTENT ONLY. Check results after actions.\n"
        "- action='click', ref='e5' → click element. Returns fresh refs if page changed.\n"
        "- action='type', ref='e3', text='hello' → type into field.\n"
        "- action='chain' → batch multiple steps: steps=['click Submit','wait 2','click Confirm']\n"
        "- action='press_key', target='Enter' for keyboard.\n\n"
        "═══ FORMS — SMART FILLING ═══\n"
        "- Page survey tells you: page type, number of inputs, buttons\n"
        "- READ field metadata: type, placeholder, required, pattern, options\n"
        "- Date fields: check placeholder for format (DD/MM/YYYY vs MM/DD/YYYY)\n"
        "- Select dropdowns: check available options before typing\n"
        "- Required fields: fill ALL required fields before submitting\n"
        "- If a field has a pattern (e.g. DNI: [0-9]{8}[A-Z]), match it exactly\n"
        "- For multi-step forms: VALIDATE each step before moving to next\n"
        "- After submit: ALWAYS check for error messages or validation failures\n\n"
        "═══ PAYMENT DETECTION ═══\n"
        "If you detect a payment/checkout page (credit card fields, 'Pay now' button):\n"
        "- STOP and report: 'Payment page detected: [amount] at [merchant]'\n"
        "- Check vault for saved payment info: vault_get('card_number'), vault_get('card_cvc')\n"
        "- If no saved card or CVC: request user approval via your response\n"
        "- NEVER enter payment details without explicit user authorization\n\n"
        "═══ CHAIN — BATCH ACTIONS ═══\n"
        "- Use button NAMES not ref IDs: steps=['click Submit','wait 1','click OK']\n"
        "- Refs change between snapshots — names are stable\n"
        "- GOOD: steps=['click Select','wait 1','click Delete']\n"
        "- BAD:  steps=['click e51','wait 1','click e54']  ← refs may be stale!\n\n"
        "═══ ERROR RECOVERY (never repeat same failed action) ═══\n"
        "- Same action fails twice → COMPLETELY different approach\n"
        "- Element not found → read the page to see what's actually there\n"
        "- Blank page → wait, the page may still be loading\n"
        "- Login required → check if there's a login button, use saved credentials\n"
        "- CAPTCHA → report it, don't try to solve it\n"
        "- After 3 failures on same step → web_search for alternative approach\n\n"
        "═══ SITE KNOWLEDGE ═══\n"
        "- Task MAY include '--- Site Knowledge ---' from previous visits\n"
        "- Use as hints, not gospel. If they don't work, ADAPT.\n\n"
        "═══ CRITICAL RULES ═══\n"
        "- NEVER tell the user to do something — YOU do it\n"
        "- NEVER give up and ask the user to do it themselves\n"
        "- If you need user INPUT (documents, credentials, choices), ask specifically\n"
        "- Always report real counts and outcomes, never fabricate\n"
        "- If partially done, report what worked and what's left"
    ),
    allowed_skills=(
        "browser", "web_search", "save_site_login", "payment",
        # Escape hatches so the specialist isn't trapped on browser when a
        # better tool exists. `google_run_task` covers Sheets/Drive/Gmail/
        # Calendar atomically; `search_tools` lets the worker discover any
        # other MCP tool the user has connected.
        "search_tools", "google_run_task",
    ),
    preferred_model="smart",  # Resolved by runner to config.worker_model
    is_builtin=True,
    include_scraper=True,
)

CODE_SPECIALIST = SpecialistConfig(
    name="code_specialist",
    display_name="Code Specialist",
    system_prompt=(
        "You are a code and skill development specialist. Your expertise is writing Python code, "
        "creating new skills, debugging logic, and performing calculations.\n\n"
        "EXECUTION LADDER (always top-down — never skip rungs, never go directly to template):\n"
        "1. Claude Code MCP — PRIMARY. Persistent session, never loses track of "
        "   prior context, full agentic loop (write → run → test → fix). Use for "
        "   ALL multi-step coding: refactors, multi-file changes, debugging, test-write-iterate.\n"
        "2. Claude via EcoRouter — FALLBACK. Use when MCP is unavailable or returned an error. "
        "   Routes through the user's active mode (CLAUDE → subscription via SDK or CLI, "
        "   HYBRID/FULL → API). Reliable for short standalone tasks: single function, single bug fix, single proposal letter.\n"
        "3. Template / fallback prose — DEEP FALLBACK ONLY. Use only when both above "
        "   are unreachable. Never short-circuit to this rung if MCP or EcoRouter is alive.\n\n"
        "When invoked: state which rung you're on, then deliver the implementation. "
        "Explain your approach briefly, focus on clean working code."
    ),
    allowed_skills=("calculate", "create_skill", "list_skills", "delete_skill"),
    preferred_model=None,
    is_builtin=True,
)

RESEARCH_SPECIALIST = SpecialistConfig(
    name="research_specialist",
    display_name="Research Specialist",
    system_prompt=(
        "You are a research and information gathering specialist.\n\n"
        "TOOL LADDER — climb top-to-bottom, stop at the first rung that answers:\n"
        "1. `web_search` for any general lookup (prices, facts, addresses, "
        "handles, news). Scraper-backed Google, free, JS-rendered. Use "
        "Google search operators for precision: "
        "`site:instagram.com <name>` (handle is in the URL — read it from "
        "the result), `\"exact phrase\"`, `intitle:` / `inurl:`.\n"
        "2. `mcp-scraper` tools (auto-injected when scraper is connected; "
        "names look like `mcp_*_extract_entities`, `mcp_*_crawl_url`, "
        "`mcp_*_deep_crawl_site`, `mcp_*_intelligent_extract`, "
        "`mcp_*_batch_crawl`, `mcp_*_search_and_crawl`):\n"
        "     - Email / phone / socials from a known URL → "
        "`extract_entities(url, entity_types=[\"email\", \"phone\"])`.\n"
        "     - Full page text in markdown → `crawl_url(url)`.\n"
        "     - Multiple similar URLs → `batch_crawl(urls=[...])`.\n"
        "     - One query → many pages → extract → "
        "`search_and_crawl(query, ...)`.\n"
        "     Skip scraper for instagram.com / facebook.com / linkedin.com "
        "— same anti-bot wall as browser.\n"
        "3. `read_file` / `list_directory` for local files.\n"
        "4. Browser ONLY when scraper genuinely can't read the field "
        "(login wall, stateful click flow). One page, extract, move on.\n\n"
        "BUDGET: max 5 tool calls per task. If the answer isn't surfacing, "
        "stop and report \"Not found\" with one line on what you tried — "
        "do not loop varying queries.\n\n"
        "NEVER report numbers (prices, stats, counts) from memory — ONLY "
        "from tool results. If a tool returns no data, say so. Do not "
        "guess or estimate. Cite sources with URLs."
    ),
    allowed_skills=(
        "web_search", "browser", "read_file", "list_directory", "run_command",
        # Same escape hatches as BROWSER_SPECIALIST — `google_run_task` for
        # Workspace ops without launching Chrome, `search_tools` to find any
        # other MCP tool when web_search and the local tools aren't enough.
        "search_tools", "google_run_task",
    ),
    preferred_model=None,
    is_builtin=True,
    include_scraper=True,
)

BUILTIN_SPECIALISTS = (
    BROWSER_SPECIALIST,
    CODE_SPECIALIST,
    RESEARCH_SPECIALIST,
)


def get_defaults() -> list[SpecialistConfig]:
    """Return the 3 built-in specialist configs."""
    return list(BUILTIN_SPECIALISTS)


# ── User-defined specialist CRUD ──────────────────────────────────────


async def save_specialist(
    config: Config, user_id: str, specialist: SpecialistConfig
) -> str:
    """Save a custom specialist to the DB. Returns the record ID."""
    if specialist.is_builtin:
        raise ValueError("Cannot save a built-in specialist")

    # Check for name collision with built-ins
    builtin_names = {s.name for s in BUILTIN_SPECIALISTS}
    if specialist.name in builtin_names:
        raise ValueError(f"Name '{specialist.name}' conflicts with a built-in specialist")

    key = await get_user_dek(config, user_id)
    record_id = str(uuid4())

    encrypted_name = encrypt(specialist.name, key)
    encrypted_display = encrypt(specialist.display_name, key)
    encrypted_prompt = encrypt(specialist.system_prompt, key)
    skills_json = json.dumps(list(specialist.allowed_skills))

    async with db_session(config) as db:
        # Upsert: delete existing with same name, then insert
        existing = await db.execute(
            "SELECT id FROM specialists WHERE user_id = ? AND name = ?",
            (user_id, encrypted_name),
        )
        row = await existing.fetchone()
        if row:
            record_id = row[0]
            await db.execute(
                "UPDATE specialists SET display_name = ?, system_prompt = ?, "
                "allowed_skills = ?, preferred_model = ? WHERE id = ?",
                (encrypted_display, encrypted_prompt, skills_json,
                 specialist.preferred_model, record_id),
            )
        else:
            await db.execute(
                "INSERT INTO specialists "
                "(id, user_id, name, display_name, system_prompt, allowed_skills, "
                "preferred_model, is_builtin) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                (record_id, user_id, encrypted_name, encrypted_display,
                 encrypted_prompt, skills_json, specialist.preferred_model),
            )
        await db.commit()

    logger.info("Saved specialist '%s' for user %s", specialist.name, user_id)
    return record_id


async def load_specialists(config: Config, user_id: str) -> list[SpecialistConfig]:
    """Load all specialists: built-in + user-defined (decrypted)."""
    result = list(BUILTIN_SPECIALISTS)
    key = await get_user_dek(config, user_id)

    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT name, display_name, system_prompt, allowed_skills, "
            "preferred_model FROM specialists WHERE user_id = ? AND is_builtin = 0",
            (user_id,),
        )
        user_rows = await rows.fetchall()

    for name_enc, display_enc, prompt_enc, skills_json, pref_model in user_rows:
        try:
            name = decrypt(name_enc, key) if name_enc.startswith("enc:") else name_enc
            display = decrypt(display_enc, key) if display_enc.startswith("enc:") else display_enc
            prompt = decrypt(prompt_enc, key) if prompt_enc.startswith("enc:") else prompt_enc
            skills = tuple(json.loads(skills_json))

            result.append(SpecialistConfig(
                name=name,
                display_name=display,
                system_prompt=prompt,
                allowed_skills=skills,
                preferred_model=pref_model,
                is_builtin=False,
            ))
        except Exception as exc:
            logger.warning("Failed to load specialist: %s", exc)

    return result


async def get_specialist(
    config: Config, user_id: str, name: str
) -> SpecialistConfig | None:
    """Get a single specialist by name."""
    # Check built-ins first
    for s in BUILTIN_SPECIALISTS:
        if s.name == name:
            return s

    # Check user-defined
    all_specs = await load_specialists(config, user_id)
    for s in all_specs:
        if s.name == name:
            return s
    return None


async def delete_specialist(config: Config, user_id: str, name: str) -> bool:
    """Delete a custom specialist. Returns True if deleted."""
    # Prevent deleting built-ins
    if any(s.name == name for s in BUILTIN_SPECIALISTS):
        raise ValueError("Cannot delete a built-in specialist")

    key = await get_user_dek(config, user_id)

    # Find the record by decrypting names
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name FROM specialists WHERE user_id = ? AND is_builtin = 0",
            (user_id,),
        )
        all_rows = await rows.fetchall()

    target_id = None
    for row_id, name_enc in all_rows:
        decrypted = decrypt_field(name_enc, key)
        if decrypted == name:
            target_id = row_id
            break

    if not target_id:
        return False

    async with db_session(config) as db:
        await db.execute("DELETE FROM specialists WHERE id = ? AND user_id = ?", (target_id, user_id))
        await db.commit()

    logger.info("Deleted specialist '%s' for user %s", name, user_id)
    return True
