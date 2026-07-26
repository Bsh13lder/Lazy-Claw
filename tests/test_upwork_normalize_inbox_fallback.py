"""Regression tests for the 2026-07-26 "No Upwork conversations found" loop.

Incident: the user asked "Check my last conversation in upWorke" and the
specialist rotated ``upwork_get_messages`` → ``upwork_last_conversation``
→ ``browser`` for ELEVEN MINUTES before a 300s timeout killed it.

Root cause chain:
  * The user's Brave tab was parked on a *conversation* URL, so Upwork's
    SPA never rendered the rooms-list sidebar. All 13 ``get_messages``
    calls logged "0 room elements matched" and fell into the URL-mining
    fallback, which returns a SINGLE synthetic row.
  * ``get_messages`` is typed ``-> list[dict] | dict`` and FastMCP emits
    one TextContent block per list item, so a 1-item list arrives as ONE
    block. ``MCPClient._call_tool_once`` only rebuilds a JSON array when
    ``len(parts) >= 2``, so the skill received a BARE JSON OBJECT.
    (Proven numerically: ``json.dumps(synthetic, indent=2)`` == 257 chars
    == the logged ``result_len=257``.)
  * ``_normalize_inbox`` did ``raw.get("result") or raw.get("messages")
    or []`` — the synthetic row has neither key → ``[]`` → the skill
    returned "No Upwork conversations found." (exactly 92 chars == the
    logged ``result_len=92``).

The specialist meanwhile called ``upwork_get_conversation`` directly and
got 1254 chars / 20 REAL bubbles, twice. So it held real data AND an
authoritative "no conversations exist" in the same turn — a plausible
negative that reads as data. That contradiction drove the loop.

Fixing at the CONSUMER (here) rather than in ``mcp/client.py`` is
deliberate: relaxing that ``>= 2`` guard to ``>= 1`` would wrap every
legitimate single-object MCP return in a spurious array and break
unrelated tools.
"""

from __future__ import annotations

import json

from lazyclaw.skills.builtin.survival.upwork_last_conversation import (
    _blocked_diagnosis,
    _normalize_inbox,
)

# The exact row mcp-upwork produced during the incident. Note
# contact_name == "" — falsy, but the row is REAL. Any truthiness test
# on contact_name silently reintroduces the bug.
INCIDENT_ROW = {
    "room_id": "room_e09f7619c1f68dd8b44544c5c4433c97",
    "room_url": (
        "https://www.upwork.com/ab/messages/rooms/"
        "room_e09f7619c1f68dd8b44544c5c4433c97"
    ),
    "contact_name": "",
    "source": "page_url_fallback",
}


class TestSingleRowCoercion:
    """A bare one-row dict must survive as a one-element list."""

    def test_incident_row_as_dict_becomes_one_element_list(self):
        assert _normalize_inbox(INCIDENT_ROW) == [INCIDENT_ROW]

    def test_incident_row_as_json_string_becomes_one_element_list(self):
        # This is the real entry shape: the MCP bridge hands the skill a
        # STRING, not a dict. (The incident logged result_len=257; this
        # fixture serialises to 207 because the real room_url carried a
        # `?companyReference=...` query string not captured in the log
        # excerpt. The byte count is incidental — the coercion is what
        # matters, so it is deliberately not asserted here.)
        payload = json.dumps(INCIDENT_ROW, indent=2)
        assert _normalize_inbox(payload) == [INCIDENT_ROW]

    def test_row_with_room_id_only(self):
        row = {"room_id": "room_abc"}
        assert _normalize_inbox(row) == [row]

    def test_row_with_named_contact(self):
        row = {"room_id": "room_x", "contact_name": "James Blue"}
        assert _normalize_inbox(row) == [row]

    def test_contact_name_present_but_empty_still_counts(self):
        # Guards the `is not None` vs truthiness distinction explicitly.
        row = {"contact_name": ""}
        assert _normalize_inbox(row) == [row]


class TestEnvelopeRegression:
    """Existing envelope shapes must keep working unchanged."""

    def test_result_envelope_unwraps(self):
        rows = [{"room_id": "a"}, {"room_id": "b"}]
        assert _normalize_inbox({"result": rows}) == rows

    def test_messages_envelope_unwraps(self):
        rows = [{"room_id": "a"}]
        assert _normalize_inbox({"messages": rows}) == rows

    def test_empty_result_envelope_stays_empty(self):
        assert _normalize_inbox({"result": []}) == []

    def test_plain_list_passes_through(self):
        rows = [{"room_id": "a"}, {"room_id": "b"}]
        assert _normalize_inbox(rows) == rows

    def test_json_string_list_parses(self):
        rows = [{"room_id": "a"}]
        assert _normalize_inbox(json.dumps(rows)) == rows

    def test_non_dict_items_filtered_out(self):
        assert _normalize_inbox([{"room_id": "a"}, "junk", 42]) == [
            {"room_id": "a"}
        ]


class TestUnrelatedPayloadsStillEmpty:
    """Don't turn arbitrary dicts into fake inbox rows."""

    def test_unrelated_dict_returns_empty(self):
        assert _normalize_inbox({"foo": "bar"}) == []

    def test_empty_dict_returns_empty(self):
        assert _normalize_inbox({}) == []

    def test_unparseable_string_returns_empty(self):
        assert _normalize_inbox("not json at all") == []

    def test_none_returns_empty(self):
        assert _normalize_inbox(None) == []

    def test_blocked_payload_is_not_coerced_to_a_row(self):
        # empty_or_blocked carries `items`, not room_id/contact_name —
        # it must NOT become a phantom one-row inbox. _blocked_diagnosis
        # handles it upstream; this just proves the coercion is narrow.
        blocked = {
            "status": "empty_or_blocked",
            "items": [],
            "page_url": "https://www.upwork.com/ab/messages/rooms/",
            "page_title": "Messages",
            "diagnosis": "cloudflare_wall",
            "hint": "solve the wall",
        }
        assert _normalize_inbox(blocked) == []


class TestSidebarUnavailableIsReadableNotBlocked:
    """``sidebar_unavailable`` is a ROUTING hint, not a dead end.

    Ordering matters: ``_blocked_diagnosis`` runs BEFORE
    ``_normalize_inbox`` in ``execute()``. If it claimed every
    ``sidebar_unavailable`` payload as "blocked" it would return an
    error string and the skill would never read the thread — which is
    precisely the failure we are fixing. The synthetic row carries a
    ``room_id``, and reading it via ``upwork_get_conversation``
    demonstrably returned 20 real bubbles during the incident.

    So: a sidebar_unavailable row WITH a usable room is NOT blocked and
    must flow through to the normal read path. Only one with nothing
    readable degrades to the blocked branch.
    """

    def test_empty_or_blocked_still_detected(self):
        payload = {"status": "empty_or_blocked", "diagnosis": "cf"}
        assert _blocked_diagnosis(payload) == payload

    def test_sidebar_unavailable_with_room_is_not_blocked(self):
        payload = {
            "status": "sidebar_unavailable",
            "room_id": "room_abc",
            "hint": "read via upwork_get_conversation(room_id=...)",
        }
        assert _blocked_diagnosis(payload) is None

    def test_sidebar_unavailable_with_room_url_only_is_not_blocked(self):
        payload = {
            "status": "sidebar_unavailable",
            "room_url": "https://www.upwork.com/ab/messages/rooms/room_x",
        }
        assert _blocked_diagnosis(payload) is None

    def test_sidebar_unavailable_with_room_normalizes_to_one_row(self):
        # The load-bearing assertion: it reaches the read path.
        payload = {
            "status": "sidebar_unavailable",
            "room_id": "room_abc",
            "contact_name": "",
            "hint": "read via upwork_get_conversation(room_id=...)",
        }
        assert _normalize_inbox(payload) == [payload]

    def test_sidebar_unavailable_without_room_is_blocked(self):
        payload = {"status": "sidebar_unavailable", "hint": "no room found"}
        assert _blocked_diagnosis(payload) == payload

    def test_sidebar_unavailable_no_room_via_result_envelope(self):
        payload = {"status": "sidebar_unavailable", "hint": "no room"}
        assert _blocked_diagnosis({"result": payload}) == payload

    def test_sidebar_unavailable_no_room_as_json_string(self):
        payload = {"status": "sidebar_unavailable", "hint": "no room"}
        assert _blocked_diagnosis(json.dumps(payload)) == payload

    def test_ordinary_row_is_not_a_block(self):
        assert _blocked_diagnosis(INCIDENT_ROW) is None

    def test_unknown_status_is_not_a_block(self):
        assert _blocked_diagnosis({"status": "something_else"}) is None
