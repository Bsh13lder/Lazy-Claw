"""Apple Vision OCR — native macOS text recognition.

Wraps `ocrmac` (PyPI), which calls Apple's `VNRecognizeTextRequest` in the
Vision framework. ~100-500ms per screenshot on M-series, deterministic,
no model weights, no GPU/RAM cost, multilingual (English/Spanish/Italian/
French/German/Portuguese out of the box).

macOS-only. Callers should check `is_available()` first and fall back to
Tesseract on Linux/Windows.

Why this beats local VLMs for browser use cases:
  * 0 hallucinations — it's perception, not generation
  * 5-200x faster than the smallest VLM that gives correct answers
  * 0 RAM cost — Vision is a shared OS service
  * Bounding boxes per word/phrase, with per-region confidence scores
"""

from __future__ import annotations

import io
import logging
import os
import platform
import tempfile
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TextRegion:
    """A single text region recognized by Apple Vision.

    `bbox` is `(x, y, width, height)` in normalized [0, 1] coordinates with
    the origin at the **top-left** of the image (CSS convention). Apple's
    native API uses bottom-left origin; we flip it on the way out so
    downstream code matches HTML/CSS expectations.
    """

    text: str
    confidence: float
    bbox: tuple[float, float, float, float]


def is_available() -> bool:
    """Whether Apple Vision OCR is usable on this host."""
    if platform.system() != "Darwin":
        return False
    try:
        import ocrmac  # noqa: F401
    except ImportError:
        return False
    return True


def read_text(
    png_bytes: bytes,
    languages: tuple[str, ...] = ("en-US", "es-ES", "it-IT", "fr-FR", "de-DE", "pt-BR"),
    min_confidence: float = 0.0,
    recognition_level: str = "accurate",
) -> list[TextRegion]:
    """Run Apple Vision text recognition on raw PNG bytes.

    Returns regions sorted in natural reading order (top-to-bottom,
    then left-to-right). Returns an empty list when running on a
    non-Mac host or when no text is found.

    `recognition_level` is `"accurate"` (default, ~200ms, higher quality)
    or `"fast"` (~130ms, lower quality — for live previews).
    """
    if not is_available():
        return []

    from ocrmac import ocrmac as _ocrmac

    # ocrmac requires a file path; write the PNG to a tempfile, clean up.
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
            fh.write(png_bytes)
            tmp_path = fh.name
        annotations = _ocrmac.OCR(
            tmp_path,
            language_preference=list(languages),
            recognition_level=recognition_level,
        ).recognize()
    except Exception as exc:
        logger.debug("Apple Vision OCR failed: %s", exc, exc_info=True)
        return []
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    regions: list[TextRegion] = []
    for text, conf, bbox in annotations:
        if conf < min_confidence:
            continue
        x, y_btm, w, h = bbox
        # Flip y from Apple bottom-left origin to top-left (CSS).
        y_top = max(0.0, 1.0 - (y_btm + h))
        regions.append(
            TextRegion(
                text=text,
                confidence=float(conf),
                bbox=(float(x), float(y_top), float(w), float(h)),
            )
        )

    # Reading order: top-down, then left-to-right within a row.
    regions.sort(key=lambda r: (r.bbox[1], r.bbox[0]))
    return regions


def render_for_llm(
    regions: list[TextRegion],
    include_bbox: bool = False,
    confidence_threshold: float = 0.3,
) -> str:
    """Format OCR regions as plain text for an LLM.

    Default: one line per region, low-confidence regions filtered out,
    no coordinates (saves tokens). Pass `include_bbox=True` when the agent
    needs to click on a specific phrase.
    """
    lines: list[str] = []
    for r in regions:
        if r.confidence < confidence_threshold:
            continue
        if include_bbox:
            x, y, w, h = r.bbox
            lines.append(
                f"[{x:.3f},{y:.3f},{w:.3f},{h:.3f}] ({r.confidence:.2f}) {r.text}"
            )
        else:
            lines.append(r.text)
    return "\n".join(lines)


# Convenience for the OCR action — returns just the joined text.
def read_plaintext(png_bytes: bytes, **kwargs) -> str:
    """Run OCR and return all text joined with newlines, in reading order."""
    regions = read_text(png_bytes, **kwargs)
    return render_for_llm(regions, include_bbox=False)
