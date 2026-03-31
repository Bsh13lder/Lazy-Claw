"""Action verifier — semantic post-action verification for browser actions.

Pure Python, no LLM calls. Compares before/after page state to determine
if an action succeeded, failed, or had no visible effect.

Used by browser_skill.py to annotate tool responses with verification results
so the agent knows what happened, not just "I clicked something."

State comparison covers:
- URL change (navigation success)
- Page title change
- Target ref disappearing (clicked element gone = navigated or modal closed)
- Error elements appearing (role="alert", class*="error", error phrases)
- Element count change in main landmark
- Page content hash change (reuses watcher.py hash pattern)
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Phrases that indicate the page now shows an error
_ERROR_PHRASES = frozenset({
    "error", "failed", "invalid", "incorrect", "unauthorized",
    "forbidden", "not found", "try again", "something went wrong",
    "access denied", "permission denied",
})

# JS: extract visible page text for content hashing (mirrors watcher.py pattern)
_JS_CONTENT_TEXT = """
(() => {
    const sel = ['main', 'article', '[role="main"]', '.content', '#content', 'body'];
    for (const s of sel) {
        const el = document.querySelector(s);
        if (el && el.innerText.trim().length > 50) {
            return el.innerText.trim().substring(0, 3000);
        }
    }
    return document.body?.innerText?.substring(0, 3000) || '';
})()
"""

# JS: extract any error/alert text present on the page
_JS_ERROR_TEXT = """
(() => {
    const texts = [];
    document.querySelectorAll('[role="alert"], [role="status"]').forEach(el => {
        const t = el.innerText.trim();
        if (t) texts.push(t);
    });
    document.querySelectorAll('[class*="error"], [class*="Error"], [class*="invalid"]').forEach(el => {
        const t = el.innerText.trim();
        if (t && t.length < 200) texts.push(t);
    });
    return texts.slice(0, 5).join(' | ');
})()
"""


# ── Data models ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BrowserState:
    """Minimal page state captured before and after an action."""

    url: str
    title: str
    element_count: int        # total interactive elements on page
    main_element_count: int   # elements in the 'main' landmark specifically
    content_hash: str         # short hash of visible page text
    target_ref_present: bool  # whether the target ref ID is in the snapshot


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of post-action semantic verification."""

    succeeded: bool
    evidence: str    # what changed (or what didn't) — shown to the LLM
    suggestion: str  # what to try next if failed (empty string when succeeded)

    def format(self, action_desc: str = "") -> str:
        """Format as a one-line annotation appended to the tool response.

        Example outputs:
          "Clicked [e5] → SUCCESS: page navigated to /inbox"
          "Clicked [e5] → FAILED: page unchanged. Try: take a snapshot to check refs."
        """
        prefix = f"{action_desc} → " if action_desc else ""
        if self.succeeded:
            return f"{prefix}SUCCESS: {self.evidence}"
        parts = [f"{prefix}FAILED: {self.evidence}"]
        if self.suggestion:
            parts.append(f"Try: {self.suggestion}")
        return ". ".join(parts)


# ── ActionVerifier ─────────────────────────────────────────────────────────


class ActionVerifier:
    """Verifies whether a browser action achieved its goal.

    Compares BrowserState snapshots taken before and after an action.
    Zero LLM calls — pure Python heuristics.
    """

    def verify(
        self,
        before: BrowserState,
        after: BrowserState,
        intended_action: str,
        target_ref: str = "",
        error_text: str = "",
    ) -> VerificationResult:
        """Verify an action result.

        Args:
            before: state captured before the action
            after: state captured after the action
            intended_action: one of "click", "type", "open", "press_key"
            target_ref: the ref ID that was acted on (for click/type)
            error_text: any error/alert text found on the page after the action

        Returns:
            VerificationResult with succeeded, evidence, suggestion
        """
        if intended_action == "open":
            return self._verify_navigation(before, after, error_text)
        elif intended_action == "click":
            return self._verify_click(before, after, target_ref, error_text)
        elif intended_action == "type":
            return self._verify_type(before, after, target_ref, error_text)
        elif intended_action == "press_key":
            return self._verify_press_key(before, after, error_text)
        else:
            # Unknown action — fall back to content change check
            if before.content_hash != after.content_hash:
                return VerificationResult(
                    succeeded=True,
                    evidence="page content changed",
                    suggestion="",
                )
            return VerificationResult(
                succeeded=False,
                evidence="page unchanged",
                suggestion="Take a snapshot to check current state.",
            )

    def _verify_navigation(
        self,
        before: BrowserState,
        after: BrowserState,
        error_text: str,
    ) -> VerificationResult:
        """Verify an open/navigation action."""
        if _has_error_signals(error_text):
            return VerificationResult(
                succeeded=False,
                evidence=f"navigation landed on error page: {error_text[:100]}",
                suggestion="Check if the URL is correct or if login is required.",
            )

        if before.url != after.url:
            return VerificationResult(
                succeeded=True,
                evidence=f"navigated to {after.url[:80]}",
                suggestion="",
            )

        if before.title != after.title or before.content_hash != after.content_hash:
            return VerificationResult(
                succeeded=True,
                evidence=f"page loaded: {after.title or 'content updated'}",
                suggestion="",
            )

        return VerificationResult(
            succeeded=False,
            evidence="URL and page content unchanged after navigation",
            suggestion=(
                "The page may not have loaded. Try action='snapshot' or check for errors."
            ),
        )

    def _verify_click(
        self,
        before: BrowserState,
        after: BrowserState,
        target_ref: str,
        error_text: str,
    ) -> VerificationResult:
        """Verify a click action."""
        if _has_error_signals(error_text):
            return VerificationResult(
                succeeded=False,
                evidence=f"error appeared after click: {error_text[:100]}",
                suggestion="Check snapshot to see the error details.",
            )

        if before.url != after.url:
            return VerificationResult(
                succeeded=True,
                evidence=f"click navigated to {after.url[:80]}",
                suggestion="",
            )

        # Element disappearing means the click triggered navigation or modal close
        if before.target_ref_present and not after.target_ref_present:
            ref_label = f"[{target_ref}]" if target_ref else "target element"
            return VerificationResult(
                succeeded=True,
                evidence=f"{ref_label} gone after click (navigated or modal closed)",
                suggestion="",
            )

        delta = after.main_element_count - before.main_element_count
        if delta != 0:
            direction = "added" if delta > 0 else "removed"
            return VerificationResult(
                succeeded=True,
                evidence=f"click {direction} {abs(delta)} element(s) on page",
                suggestion="",
            )

        if before.content_hash != after.content_hash:
            return VerificationResult(
                succeeded=True,
                evidence="page content changed after click",
                suggestion="",
            )

        ref_label = f" [{target_ref}]" if target_ref else ""
        return VerificationResult(
            succeeded=False,
            evidence=f"page unchanged after clicking{ref_label}",
            suggestion=(
                "Take a snapshot to verify the ref is still valid, "
                "or try a different element."
            ),
        )

    def _verify_type(
        self,
        before: BrowserState,
        after: BrowserState,
        target_ref: str,
        error_text: str,
    ) -> VerificationResult:
        """Verify a type action."""
        if _has_error_signals(error_text):
            return VerificationResult(
                succeeded=False,
                evidence=f"error after typing: {error_text[:100]}",
                suggestion="Check snapshot to see the error.",
            )

        if before.content_hash != after.content_hash:
            return VerificationResult(
                succeeded=True,
                evidence="page content changed after typing (text accepted)",
                suggestion="",
            )

        ref_label = f" [{target_ref}]" if target_ref else ""
        return VerificationResult(
            succeeded=False,
            evidence=f"page unchanged after typing into{ref_label}",
            suggestion=(
                "Text may not have reached the field. "
                "Try clicking the element first to focus it, then type again."
            ),
        )

    def _verify_press_key(
        self,
        before: BrowserState,
        after: BrowserState,
        error_text: str,
    ) -> VerificationResult:
        """Verify a press_key action."""
        if _has_error_signals(error_text):
            return VerificationResult(
                succeeded=False,
                evidence=f"error after key press: {error_text[:100]}",
                suggestion="Check snapshot for error details.",
            )

        if before.url != after.url:
            return VerificationResult(
                succeeded=True,
                evidence=f"key press navigated to {after.url[:80]}",
                suggestion="",
            )

        if before.content_hash != after.content_hash:
            return VerificationResult(
                succeeded=True,
                evidence="page content changed after key press",
                suggestion="",
            )

        return VerificationResult(
            succeeded=False,
            evidence="page unchanged after key press",
            suggestion=(
                "The key press had no visible effect. "
                "Check if the right element is focused."
            ),
        )


# ── State capture helpers ──────────────────────────────────────────────────


async def capture_state(
    backend,
    snapshot_manager,
    target_ref: str = "",
) -> BrowserState:
    """Capture minimal page state for before/after comparison.

    Uses the cached snapshot if available to avoid an extra CDP round-trip.
    Only makes CDP calls for url, title, and content hash.
    """
    url = ""
    title = ""
    element_count = 0
    main_element_count = 0
    target_ref_present = False

    # Prefer cached snapshot (no CDP call) for element data
    snap = snapshot_manager.current
    if snap is not None:
        url = snap.url
        title = snap.title
        element_count = snap.element_count
        for lm in snap.landmarks:
            if lm.name == "main":
                main_element_count = len(lm.ref_ids)
                break
        if target_ref:
            target_ref_present = target_ref in snap.elements
    else:
        # No snapshot available — fetch url/title from CDP directly
        try:
            url = await backend.current_url() or ""
        except Exception:
            pass
        try:
            title = await backend.title() or ""
        except Exception:
            pass

    # Content hash: one CDP evaluate() — cheap and reliable
    content_text = ""
    try:
        content_text = await backend.evaluate(_JS_CONTENT_TEXT) or ""
    except Exception:
        pass

    return BrowserState(
        url=url,
        title=title,
        element_count=element_count,
        main_element_count=main_element_count,
        content_hash=_content_hash(str(content_text)),
        target_ref_present=target_ref_present,
    )


async def capture_state_fresh(
    backend,
    snapshot_manager,
    target_ref: str = "",
) -> BrowserState:
    """Capture state using live CDP url/title (for after-action comparison).

    After navigation or click, the cached snapshot url/title may be stale.
    This version always fetches url/title from CDP.
    """
    url = ""
    title = ""
    element_count = 0
    main_element_count = 0
    target_ref_present = False

    try:
        url = await backend.current_url() or ""
    except Exception:
        pass
    try:
        title = await backend.title() or ""
    except Exception:
        pass

    # Element counts from current snapshot (may be fresh after action methods took one)
    snap = snapshot_manager.current
    if snap is not None:
        element_count = snap.element_count
        for lm in snap.landmarks:
            if lm.name == "main":
                main_element_count = len(lm.ref_ids)
                break
        if target_ref:
            target_ref_present = target_ref in snap.elements

    content_text = ""
    try:
        content_text = await backend.evaluate(_JS_CONTENT_TEXT) or ""
    except Exception:
        pass

    return BrowserState(
        url=url,
        title=title,
        element_count=element_count,
        main_element_count=main_element_count,
        content_hash=_content_hash(str(content_text)),
        target_ref_present=target_ref_present,
    )


async def capture_error_text(backend) -> str:
    """Extract any error/alert text visible on the page after an action."""
    try:
        result = await backend.evaluate(_JS_ERROR_TEXT)
        return str(result or "")
    except Exception:
        return ""


# ── Internal helpers ───────────────────────────────────────────────────────


def _content_hash(text: str) -> str:
    """Short SHA-256 hash for change detection (same pattern as watcher.py)."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _has_error_signals(error_text: str) -> bool:
    """Return True if the error text contains known failure phrases."""
    if not error_text:
        return False
    lower = error_text.lower()
    return any(phrase in lower for phrase in _ERROR_PHRASES)
