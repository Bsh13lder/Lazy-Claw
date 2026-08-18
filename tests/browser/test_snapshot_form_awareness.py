"""Form-aware snapshots (2026-08-18 himap add-form incident).

The specialist burned 40+ steps scroll-hunting a Django admin form because:
(1) form fields have no aria-label/placeholder and the namer never looked at
``<label for>`` → every input rendered as a NAMELESS "textbox"; (2) values
were invisible → it could not tell filled from empty and re-verified
endlessly; (3) ``checked`` was emitted only when true → "unchecked" was
indistinguishable from "not a checkbox", and it clicked is_published TWICE,
silently un-publishing the post.
"""

from __future__ import annotations

from lazyclaw.browser.snapshot import (
    _FALLBACK_VERSION,
    _JS_INJECT_FALLBACK,
    ElementRef,
    _format_element,
)


def _el(**kw):
    defaults = dict(
        ref_id="e5", role="textbox", name="", tag="input",
        landmark="form", properties=(),
    )
    defaults.update(kw)
    return ElementRef(**defaults)


class TestFormatElement:
    def test_value_is_rendered(self):
        el = _el(name="Title", properties=(("value", "My Post"),))
        out = _format_element(el)
        assert 'value="My Post"' in out
        assert '"Title"' in out

    def test_unchecked_checkbox_shows_false(self):
        el = _el(role="checkbox", properties=(("checked", "false"), ("type", "checkbox")))
        out = _format_element(el)
        assert "checked=false" in out

    def test_checked_checkbox_shows_true(self):
        el = _el(role="checkbox", properties=(("checked", "true"), ("type", "checkbox")))
        assert "checked=true" in _format_element(el)

    def test_required_is_rendered(self):
        el = _el(properties=(("required", "true"),))
        assert "required=true" in _format_element(el)


class TestFallbackJsContract:
    """Source pins on the injected JS — no DOM available in unit tests."""

    def test_namer_resolves_label_elements(self):
        assert "el.labels" in _JS_INJECT_FALLBACK
        assert "closest && el.closest('label')" in _JS_INJECT_FALLBACK

    def test_namer_falls_back_to_name_attribute(self):
        assert "getAttribute('name')" in _JS_INJECT_FALLBACK

    def test_checkboxes_report_both_states(self):
        assert "el.checked ? 'true' : 'false'" in _JS_INJECT_FALLBACK

    def test_values_are_captured_for_text_and_select(self):
        assert "p.value = String(el.value).slice(0,40)" in _JS_INJECT_FALLBACK
        assert "selectedOptions" in _JS_INJECT_FALLBACK

    def test_version_bumped_and_consistent(self):
        # Changing the JS without bumping both versions leaves stale engines
        # cached in long-lived tabs.
        assert _FALLBACK_VERSION >= 5
        assert f"EXPECTED_VERSION = {_FALLBACK_VERSION}" in _JS_INJECT_FALLBACK
