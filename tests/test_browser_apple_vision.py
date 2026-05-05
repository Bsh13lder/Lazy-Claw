"""Tests for lazyclaw.browser.apple_vision — Apple Vision OCR wrapper.

macOS-only — tests are skipped on other platforms or when ocrmac isn't
installed. Uses a tiny PNG generated at runtime so no fixtures are needed.
"""

from __future__ import annotations

import io
import platform

import pytest

from lazyclaw.browser import apple_vision


def _make_text_png(text: str = "Hello LazyClaw", width: int = 600, height: int = 200) -> bytes:
    """Render `text` onto a white PNG using PIL (already a project dep)."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    # Use the default bitmap font — present everywhere, no system font deps.
    try:
        font = ImageFont.load_default(size=48)
    except (TypeError, AttributeError):
        font = ImageFont.load_default()
    draw.text((20, 60), text, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="Apple Vision is macOS-only",
)
class TestAppleVisionAvailability:
    def test_is_available_on_mac(self):
        assert apple_vision.is_available() is True


class TestAppleVisionFallbackOnNonMac:
    def test_read_text_returns_empty_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(apple_vision, "is_available", lambda: False)
        assert apple_vision.read_text(b"\x89PNG fake") == []

    def test_read_plaintext_returns_empty_string_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(apple_vision, "is_available", lambda: False)
        assert apple_vision.read_plaintext(b"\x89PNG fake") == ""


@pytest.mark.skipif(
    not apple_vision.is_available(),
    reason="ocrmac not installed or non-Mac platform",
)
class TestAppleVisionRecognition:
    def test_reads_simple_text_from_png(self):
        png = _make_text_png("Hello LazyClaw")
        regions = apple_vision.read_text(png)
        assert len(regions) >= 1
        joined = " ".join(r.text for r in regions).lower()
        # Apple Vision is robust enough on default fonts that we expect
        # both words back (case-insensitive — VLMs sometimes title-case).
        assert "hello" in joined
        assert "lazyclaw" in joined

    def test_bbox_origin_is_top_left(self):
        # Render the same text at top vs bottom and confirm y increases downward.
        png_top = _make_text_png("MARKER", height=400)
        regions_top = apple_vision.read_text(png_top)
        assert regions_top, "expected at least one region"
        # Text was drawn at y=60 in a 400px-tall image — bbox y should be small
        # (near the top in CSS-style top-left origin coords).
        for r in regions_top:
            if "marker" in r.text.lower():
                assert r.bbox[1] < 0.5, (
                    f"expected top-region y < 0.5, got bbox={r.bbox}"
                )
                break
        else:
            pytest.fail("MARKER text not recognized")

    def test_min_confidence_filters(self):
        png = _make_text_png("Confident text")
        # Filtering at 0.99 should keep only the highest-confidence regions.
        regions_all = apple_vision.read_text(png, min_confidence=0.0)
        regions_strict = apple_vision.read_text(png, min_confidence=0.99)
        assert len(regions_strict) <= len(regions_all)
        for r in regions_strict:
            assert r.confidence >= 0.99

    def test_read_plaintext_returns_lines(self):
        png = _make_text_png("Plain text test")
        text = apple_vision.read_plaintext(png)
        assert isinstance(text, str)
        assert "plain" in text.lower()

    def test_empty_image_returns_empty_list(self):
        # Solid white PNG with no text — should produce no regions.
        from PIL import Image

        img = Image.new("RGB", (200, 200), "white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        regions = apple_vision.read_text(buf.getvalue())
        # Apple Vision sometimes returns spurious empty-looking regions;
        # filter low-confidence and verify the high-confidence set is empty.
        high_conf = [r for r in regions if r.confidence > 0.5]
        assert high_conf == []


class TestRenderForLLM:
    def test_renders_one_text_per_line(self):
        regions = [
            apple_vision.TextRegion("First", 1.0, (0.0, 0.1, 0.2, 0.05)),
            apple_vision.TextRegion("Second", 0.9, (0.0, 0.3, 0.2, 0.05)),
        ]
        out = apple_vision.render_for_llm(regions, include_bbox=False)
        assert out == "First\nSecond"

    def test_filters_low_confidence(self):
        regions = [
            apple_vision.TextRegion("Keep", 0.9, (0.0, 0.1, 0.2, 0.05)),
            apple_vision.TextRegion("Drop", 0.1, (0.0, 0.3, 0.2, 0.05)),
        ]
        out = apple_vision.render_for_llm(regions, confidence_threshold=0.5)
        assert out == "Keep"

    def test_includes_bbox_when_requested(self):
        regions = [apple_vision.TextRegion("Hi", 1.0, (0.1, 0.2, 0.3, 0.05))]
        out = apple_vision.render_for_llm(regions, include_bbox=True)
        assert "0.100" in out and "0.200" in out and "Hi" in out
