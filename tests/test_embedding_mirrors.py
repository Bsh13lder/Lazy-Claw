"""Unit tests for the LazyBrain mirror layer.

Covers:
  - browser/site_memory.py::_render_content_as_bullets — recursive
    walker that turned the JSON dump into bullet prose.
  - browser/templates.py::_build_template_note_body — renders a
    template dict as the LazyBrain mirror body.
  - browser/templates.py::_slugify — used in the template/<slug> tag.
  - lazybrain/canvas.py::_extract_canvas_text — pulls text-bearing
    fields out of React Flow node `data` dicts.

The actual mirror coroutines (`_mirror_template_note`,
`_mirror_canvas_note`) are exercised indirectly: their happy path
just calls into the LazyBrain store, which is covered by existing
LazyBrain integration tests. We test the pure-function helpers that
shape the embedding-relevant text — the part that actually changes
recall quality.
"""
from __future__ import annotations

from lazyclaw.browser.site_memory import _render_content_as_bullets
from lazyclaw.browser.templates import (
    _build_template_note_body,
    _slugify,
)
from lazyclaw.lazybrain.canvas import _extract_canvas_text


# ── _render_content_as_bullets ─────────────────────────────────────


def test_render_flat_dict_as_bullets():
    out = _render_content_as_bullets({
        "selector": "#login-btn",
        "label": "Sign in",
        "url": "https://example.com/login",
    })
    assert "- selector: #login-btn" in out
    assert "- label: Sign in" in out
    assert "- url: https://example.com/login" in out
    # Crucially: no JSON braces — that was the embedding-noise issue.
    assert "{" not in out and "}" not in out


def test_render_nested_dict_indents():
    out = _render_content_as_bullets({
        "domain": "upwork.com",
        "form": {"username": "#email", "password": "#pw"},
    })
    assert "- domain: upwork.com" in out
    assert "- form:" in out
    # Nested entries indent with 2 spaces.
    assert "  - username: #email" in out
    assert "  - password: #pw" in out


def test_render_list_of_dicts():
    out = _render_content_as_bullets({
        "steps": [
            {"action": "click", "target": "#login"},
            {"action": "type",  "target": "#email"},
        ],
    })
    assert "- steps:" in out
    assert "- item 1:" in out
    assert "  - action: click" in out
    assert "- item 2:" in out
    assert "  - action: type" in out


def test_render_truncates_at_max_chars():
    huge_value = "X" * 5000
    out = _render_content_as_bullets({"big": huge_value})
    assert len(out) <= 1500 + len("\n…(truncated)") + 1
    assert "…(truncated)" in out


def test_render_skips_empty_strings():
    """Empty string values produce no bullet — keeps embedding clean."""
    out = _render_content_as_bullets({
        "good": "value",
        "bad": "",
        "alsogood": "x",
    })
    assert "- good: value" in out
    assert "- alsogood: x" in out
    # Empty value should NOT produce "- bad: " line.
    assert "- bad:" not in out


# ── _build_template_note_body ──────────────────────────────────────


def test_template_body_renders_all_sections():
    tpl = {
        "name": "Cita Previa Spain",
        "icon": "🇪🇸",
        "setup_urls": ["https://sede.administracionespublicas.gob.es/"],
        "checkpoints": ["Pick date", "Confirm booking"],
        "playbook": "Use NIE from vault. Pick first available slot in Madrid.",
        "system_prompt": "You are an appointment booking assistant.",
    }
    body = _build_template_note_body(tpl)
    assert "Browser template:" in body
    assert "Cita Previa Spain" in body
    assert "🇪🇸" in body
    assert "https://sede.administracionespublicas.gob.es/" in body
    assert "Pick date" in body
    assert "Confirm booking" in body
    assert "Use NIE from vault" in body
    assert "appointment booking assistant" in body


def test_template_body_truncates_long_playbook():
    tpl = {
        "name": "Long Template",
        "playbook": "step. " * 1000,  # 6000 chars
        "system_prompt": "short",
    }
    body = _build_template_note_body(tpl)
    assert "…(truncated)" in body
    # Body still includes the head of the playbook + the system prompt.
    assert "step." in body
    assert "short" in body


def test_template_body_handles_missing_fields():
    """Skip empty sections instead of leaving "Setup URLs:\n" headers."""
    tpl = {"name": "Minimal"}
    body = _build_template_note_body(tpl)
    assert "Minimal" in body
    assert "Setup URLs" not in body
    assert "Playbook" not in body
    assert "System prompt" not in body


# ── _slugify ───────────────────────────────────────────────────────


def test_slugify_basic():
    assert _slugify("Cita Previa Spain") == "cita-previa-spain"
    assert _slugify("  Doctoralia booking  ") == "doctoralia-booking"


def test_slugify_collapses_runs():
    assert _slugify("Hello---World!!!") == "hello-world"


def test_slugify_handles_empty():
    assert _slugify("") == "untitled"
    assert _slugify("   ") == "untitled"


# ── _extract_canvas_text ───────────────────────────────────────────


def test_canvas_extract_text_nodes():
    payload = {
        "nodes": [
            {"id": "n1", "data": {"text": "Buy groceries"}},
            {"id": "n2", "data": {"label": "Pickup laundry"}},
            {"id": "n3", "data": {"content": "Call mom"}},
        ],
        "edges": [],
    }
    out = _extract_canvas_text(payload)
    assert "Buy groceries" in out
    assert "Pickup laundry" in out
    assert "Call mom" in out


def test_canvas_extract_note_reference_as_wikilink():
    payload = {
        "nodes": [
            {"id": "n1", "data": {"noteId": "abc-123", "noteTitle": "Quarterly Plan"}},
            {"id": "n2", "data": {"text": "Standalone idea"}},
        ],
    }
    out = _extract_canvas_text(payload)
    assert "[[Quarterly Plan]]" in out
    assert "Standalone idea" in out


def test_canvas_extract_skips_empty_payload():
    assert _extract_canvas_text({}) == []
    assert _extract_canvas_text({"nodes": []}) == []
    assert _extract_canvas_text({"nodes": [{"id": "n1"}]}) == []  # no data
    assert _extract_canvas_text({"nodes": [{"id": "n1", "data": {}}]}) == []


def test_canvas_extract_dedupes_repeated_text():
    payload = {
        "nodes": [
            {"id": "n1", "data": {"text": "duplicate"}},
            {"id": "n2", "data": {"text": "duplicate"}},
            {"id": "n3", "data": {"text": "unique"}},
        ],
    }
    out = _extract_canvas_text(payload)
    assert out.count("duplicate") == 1
    assert "unique" in out


def test_canvas_extract_one_field_per_node():
    """A single node with both text and label produces one snippet,
    not two — avoids embedding the same idea twice."""
    payload = {
        "nodes": [
            {"id": "n1", "data": {"text": "primary text", "label": "secondary label"}},
        ],
    }
    out = _extract_canvas_text(payload)
    # Order of _TEXT_FIELDS puts "text" before "label", so text wins.
    assert out == ["primary text"]


def test_canvas_extract_handles_malformed_input():
    """Non-dict node data shouldn't crash the walker."""
    payload = {
        "nodes": [
            {"id": "n1", "data": "string-not-dict"},
            {"id": "n2", "data": None},
            "not-a-dict",
            {"id": "n3", "data": {"text": "valid"}},
        ],
    }
    out = _extract_canvas_text(payload)
    assert out == ["valid"]
