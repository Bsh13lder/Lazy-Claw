"""Unit tests for the prompt-body ↔ allowlist drift detector.

LazyClaw's #1 recurring specialist incident is a prompt body that tells the
worker to call a tool its ``tools:`` allowlist cannot reach (2026-06-10
email, 2026-07-31 freelance ×2, 2026-08-13 browser ``use_host_browser``,
2026-08-14 messaging/email phantoms). The allowlist is the callability
contract — ``search_tools`` discovery does NOT grant callability — so a
prompt-named tool that is missing from ``tools:`` sends the specialist into
a stuck loop hunting a name it can never call.

These tests pin the extraction + validation heuristics. The repo-wide gate
lives in ``test_specialist_prompt_sweep.py``.
"""

from __future__ import annotations

from lazyclaw.teams.prompt_tool_refs import (
    META_TOOL_NAMES,
    SCRAPER_TOOL_NAMES,
    extract_prompt_tool_refs,
    validate_prompt_tool_refs,
)
from lazyclaw.teams.specialist import SpecialistConfig


def _spec(prompt: str, tools: tuple[str, ...], *, include_scraper: bool = False):
    return SpecialistConfig(
        name="t",
        display_name="T",
        system_prompt=prompt,
        allowed_skills=tools,
        include_scraper=include_scraper,
    )


# ── extract_prompt_tool_refs: the happy shapes ────────────────────────


def test_extracts_bare_backticked_tool_name():
    assert extract_prompt_tool_refs("Call `upwork_send_message` now.") == {
        "upwork_send_message"
    }


def test_extracts_head_of_a_call_span_with_arguments():
    body = 'Use `upwork_get_messages(limit=20)` first.'
    assert extract_prompt_tool_refs(body) == {"upwork_get_messages"}


def test_extracts_multiple_refs_across_one_slash_list():
    body = "Read with `email_read` / `email_search` / `email_status`."
    assert extract_prompt_tool_refs(body) == {
        "email_read",
        "email_search",
        "email_status",
    }


def test_meta_names_without_underscore_are_tool_like():
    body = "Fall back to `browser` or hand off with `delegate` / `agent`."
    assert extract_prompt_tool_refs(body) == {"browser", "delegate", "agent"}
    assert {"browser", "agent", "delegate", "search_tools"} <= META_TOOL_NAMES


def test_call_span_head_is_extracted_for_meta_names_too():
    body = 'Try `browser(action="open", url="https://x.test")`.'
    assert extract_prompt_tool_refs(body) == {"browser"}


# ── extract_prompt_tool_refs: argument names are NOT tools ────────────


def test_kwargs_inside_a_call_span_are_not_tool_refs():
    body = 'Send with `upwork_send_message(room_id=..., message=...)`.'
    assert extract_prompt_tool_refs(body) == {"upwork_send_message"}


def test_a_kwarg_named_in_prose_elsewhere_is_still_suppressed():
    """`room_id` shows up bare in prose but is a PARAMETER, not a tool.

    Any identifier the prompt ever shows in an argument position
    (``name=``) inside a backticked snippet is treated as a parameter for
    the whole body — tool names are never written that way.
    """
    body = (
        "Get a `room_id` from the read, then call "
        "`upwork_get_conversation(room_id=...)`."
    )
    assert extract_prompt_tool_refs(body) == {"upwork_get_conversation"}


def test_bare_kwarg_span_is_not_a_tool_ref():
    body = "If it would commit the user, pass `draft_only=true` instead."
    assert extract_prompt_tool_refs(body) == set()


# ── extract_prompt_tool_refs: obvious non-tools ───────────────────────


def test_non_tool_shapes_are_ignored():
    body = (
        "Storage format is `enc:v1:<nonce>:<ct>`; sources live at "
        "`path/to/file.py:line`; cron is `0 */6 * * *`; the marker is "
        "`[JOB:abc-123]`; links look like `[md](url)`; wikilinks are "
        "`[[Title]]`; search with `site:domain.com query` and "
        "`intitle:word`; a URL like `https://example.com/x_y` is not a "
        "tool; a status is `status: \"empty_or_blocked\"`; the shell is "
        "`rg -n \"symbol\" lazyclaw/`."
    )
    assert extract_prompt_tool_refs(body) == set()


def test_single_words_without_underscore_are_not_tool_like():
    body = "Use `git` to inspect, pass a `name`/`title`, then `open` the tab."
    assert extract_prompt_tool_refs(body) == set()


def test_dynamic_mcp_id_placeholders_are_ignored():
    body = "Ignore `mcp_*`, `mcp_*_crawl_url` and `mcp_…_create_sheet`."
    assert extract_prompt_tool_refs(body) == set()


def test_short_identifiers_are_ignored():
    assert extract_prompt_tool_refs("run `rm` or `rg`") == set()


def test_namespace_prefix_stubs_are_not_tool_refs():
    """A trailing underscore marks a prefix, not a callable name."""
    body = "Every LazyBrain tool carries the `lazybrain_` prefix."
    assert extract_prompt_tool_refs(body) == set()


def test_prose_without_backticks_is_never_extracted():
    body = "Call upwork_send_message when you are ready."
    assert extract_prompt_tool_refs(body) == set()


# ── extract_prompt_tool_refs: negation is sentence-scoped ─────────────


def test_negated_sentence_exempts_its_refs():
    body = "There are NO `telegram_send` tools — Telegram is a native channel."
    assert extract_prompt_tool_refs(body) == set()


def test_never_use_sentence_exempts_its_refs():
    body = (
        "NEVER use Google Sheets or any `google_run_task`, "
        "`create_google_sheet`, or `append_sheet_rows` document tool."
    )
    assert extract_prompt_tool_refs(body) == set()


def test_does_not_exist_sentence_exempts_its_refs():
    body = "No `email_read_thread` tool exists; it doesn't exist any more."
    assert extract_prompt_tool_refs(body) == set()


def test_negation_does_not_leak_into_the_next_sentence():
    """A NEVER sentence must not exempt the whole paragraph."""
    body = (
        "NEVER use `google_run_task`. To actually submit, call "
        "`upwork_submit_proposal`."
    )
    assert extract_prompt_tool_refs(body) == {"upwork_submit_proposal"}


def test_negation_does_not_leak_across_a_newline():
    body = "Do not call `google_run_task`\nUse `create_sheet` instead"
    assert extract_prompt_tool_refs(body) == {"create_sheet"}


def test_negation_only_exempts_refs_written_after_the_marker():
    """A trailing "…with no room" must not retro-exempt an earlier name.

    Order-scoping keeps the gate honest: only tools the prompt names AFTER
    it starts forbidding are treated as forbidden.
    """
    body = "Retry with `email_send` when there is no thread id."
    assert extract_prompt_tool_refs(body) == {"email_send"}


# ── validate_prompt_tool_refs ─────────────────────────────────────────


def test_validate_flags_a_prompt_named_tool_missing_from_the_allowlist():
    spec = _spec(
        "Read with `upwork_last_conversation`, then `upwork_send_message`.",
        ("upwork_last_conversation",),
    )
    assert validate_prompt_tool_refs(spec) == ["upwork_send_message"]


def test_validate_passes_when_every_named_tool_is_allowlisted():
    spec = _spec(
        "Read with `email_read`, send with `email_send`.",
        ("email_read", "email_send"),
    )
    assert validate_prompt_tool_refs(spec) == []


def test_validate_matches_by_bare_mcp_suffix():
    """MCP ids carry a dynamic ``mcp_<uuid>_`` prefix; the runtime matches
    the bare suffix, so the validator must too (``teams/runner.py``)."""
    spec = _spec(
        "Submit with `upwork_submit_proposal`.",
        ("mcp_489c8963-cdc0-4937-8470-15e6ba9b6e4c_upwork_submit_proposal",),
    )
    assert validate_prompt_tool_refs(spec) == []


def test_validate_returns_sorted_deterministic_output():
    spec = _spec("`zeta_tool` then `alpha_tool` then `mid_tool`", ())
    assert validate_prompt_tool_refs(spec) == [
        "alpha_tool",
        "mid_tool",
        "zeta_tool",
    ]


def test_validate_skips_wildcard_allowlists():
    """``tools: "*"`` reaches every registry tool — nothing to drift from."""
    spec = _spec("Call `anything_at_all`.", ("*",))
    assert validate_prompt_tool_refs(spec) == []


def test_validate_allows_scraper_tools_when_include_scraper():
    """``include_scraper: true`` unions mcp-scraper tools in at runtime
    (``teams/runner.py::_filter_tools``), so naming them is legitimate."""
    spec = _spec("Crawl with `crawl_url`.", ("web_search",), include_scraper=True)
    assert validate_prompt_tool_refs(spec) == []
    assert "crawl_url" in SCRAPER_TOOL_NAMES


def test_validate_flags_scraper_tools_without_include_scraper():
    spec = _spec("Crawl with `crawl_url`.", ("web_search",), include_scraper=False)
    assert validate_prompt_tool_refs(spec) == ["crawl_url"]


def test_validate_accepts_extra_known_names():
    spec = _spec("Call `runtime_injected_tool`.", ())
    assert validate_prompt_tool_refs(
        spec, known_extra=("runtime_injected_tool",)
    ) == []
