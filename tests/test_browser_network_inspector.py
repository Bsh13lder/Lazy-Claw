"""Tests for the browser network inspector ring buffer."""

from __future__ import annotations

from lazyclaw.browser import network_inspector


def _fresh_user(tag: str) -> str:
    # Each test gets an isolated user_id so we don't leak state across tests.
    return f"test-{tag}"


def test_query_on_empty_returns_empty():
    records, truncated, total = network_inspector.query(_fresh_user("empty"))
    assert records == []
    assert truncated is False
    assert total == 0


def test_request_then_response_then_finished():
    uid = _fresh_user("basic")
    network_inspector.clear(uid)
    network_inspector.record_request(uid, "req-1", "https://example.com/api/x", "GET")
    network_inspector.record_response(uid, "req-1", 200, "application/json", 512, False)
    network_inspector.record_finished(uid, "req-1", 512)

    records, truncated, total = network_inspector.query(uid)
    assert total == 1
    assert len(records) == 1
    r = records[0]
    assert r.url == "https://example.com/api/x"
    assert r.method == "GET"
    assert r.status == 200
    assert r.mime_type == "application/json"
    assert r.response_size == 512
    assert r.response_ts is not None
    assert r.failed is False
    assert truncated is False


def test_url_substring_filter_case_insensitive():
    uid = _fresh_user("filter")
    network_inspector.clear(uid)
    network_inspector.record_request(uid, "a", "https://api.example.com/users", "GET")
    network_inspector.record_request(uid, "b", "https://cdn.example.com/logo.png", "GET")

    records, _, total = network_inspector.query(uid, url_substring="Users")
    assert total == 1
    assert records[0].url.endswith("/users")


def test_status_range_filter():
    uid = _fresh_user("status")
    network_inspector.clear(uid)
    for i, (rid, status) in enumerate([("a", 200), ("b", 404), ("c", 500)]):
        network_inspector.record_request(uid, rid, f"https://x/{i}", "GET")
        network_inspector.record_response(uid, rid, status, "text/html", 10, False)

    records, _, _ = network_inspector.query(uid, status_min=400)
    assert sorted(r.status for r in records) == [404, 500]


def test_only_failed_filter():
    uid = _fresh_user("failed")
    network_inspector.clear(uid)
    network_inspector.record_request(uid, "ok", "https://x/ok", "GET")
    network_inspector.record_response(uid, "ok", 200, "text/html", 5, False)
    network_inspector.record_request(uid, "bad", "https://x/bad", "GET")
    network_inspector.record_failed(uid, "bad", "net::ERR_CONNECTION_REFUSED")

    records, _, total = network_inspector.query(uid, only_failed=True)
    assert total == 1
    assert records[0].failed is True
    assert records[0].error_text == "net::ERR_CONNECTION_REFUSED"


def test_records_are_returned_newest_first():
    uid = _fresh_user("order")
    network_inspector.clear(uid)
    for i in range(3):
        network_inspector.record_request(uid, f"r{i}", f"https://x/{i}", "GET")

    records, _, _ = network_inspector.query(uid)
    # Newest first
    assert [r.request_id for r in records] == ["r2", "r1", "r0"]


def test_ring_buffer_evicts_oldest_and_flags_truncated():
    uid = _fresh_user("ring")
    network_inspector.clear(uid)
    # Fill well beyond the bound
    for i in range(120):
        network_inspector.record_request(uid, f"r{i}", f"https://x/{i}", "GET")

    records, truncated, total = network_inspector.query(uid, limit=50)
    assert truncated is True
    assert total == 100  # ring is capped at 100
    # First entry should be the newest (r119), never r0
    assert records[0].request_id == "r119"


def test_per_user_isolation():
    a = _fresh_user("iso-a")
    b = _fresh_user("iso-b")
    network_inspector.clear(a)
    network_inspector.clear(b)
    network_inspector.record_request(a, "aa", "https://x/a", "GET")
    network_inspector.record_request(b, "bb", "https://x/b", "GET")

    records_a, _, _ = network_inspector.query(a)
    records_b, _, _ = network_inspector.query(b)
    assert [r.request_id for r in records_a] == ["aa"]
    assert [r.request_id for r in records_b] == ["bb"]


def test_limit_clamp_and_no_panic_on_huge_limit():
    uid = _fresh_user("limit")
    network_inspector.clear(uid)
    for i in range(10):
        network_inspector.record_request(uid, f"r{i}", f"https://x/{i}", "GET")

    records, _, total = network_inspector.query(uid, limit=5)
    assert len(records) == 5
    assert total == 10
