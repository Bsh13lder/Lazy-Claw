"""Browser interaction actions — click, type, press_key, hover, drag.

Extracted from browser_skill.py for maintainability.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re

from lazyclaw.browser.action_errors import (
    RETRY_GIVE_UP,
    RETRY_RE_READ,
    ActionError,
    ActionErrorCode,
)
from lazyclaw.browser.action_verifier import (
    capture_error_text,
    capture_state,
    capture_state_fresh,
)

from .backends import get_backend
from .human_actions import human_click_ref, human_type_text
from .read_open import element_not_found_hint

logger = logging.getLogger(__name__)


async def action_click(
    user_id: str, params: dict, tab_context, snapshot_mgr, verifier,
) -> str:
    """Click an element by ref ID, CSS selector, or natural description."""
    ref = (params.get("ref") or "").strip()
    target = (params.get("target") or "").strip()

    if not ref and not target:
        return str(ActionError(
            code=ActionErrorCode.POLICY_DENIED,
            message="click requires either ref or target.",
            hint="Pass ref='e5' from a prior snapshot, or target='CSS selector / natural description'.",
            retry_strategy=RETRY_GIVE_UP,
        ))

    backend = await get_backend(user_id, tab_context)

    # Ref-ID path (preferred)
    if ref:
        _before_state = None
        try:
            _before_state = await capture_state(backend, snapshot_mgr, target_ref=ref)
        except Exception as exc:
            logger.debug("Failed to capture pre-click state: %s", exc)
        meta = await snapshot_mgr.get_ref_meta(backend, ref)
        clicked = await human_click_ref(backend, snapshot_mgr, ref)
        if clicked:
            await asyncio.sleep(random.uniform(0.2, 0.8))
            name = meta.get("name", ref) if meta else ref
            role = meta.get("role", "") if meta else ""
            confirm = f"Clicked: [{ref}] {role} \"{name}\""
            if await snapshot_mgr.is_stale(backend):
                snapshot = await snapshot_mgr.take_snapshot(backend)
                confirm = f"{confirm}\n\n{snapshot_mgr.format_snapshot(snapshot)}"
            if _before_state is not None:
                try:
                    _error_text = await capture_error_text(backend)
                    _after_state = await capture_state_fresh(backend, snapshot_mgr, target_ref=ref)
                    _vr = verifier.verify(
                        _before_state, _after_state, "click",
                        target_ref=ref, error_text=_error_text,
                    )
                    confirm += f"\n{_vr.format(f'Clicked [{ref}]')}"
                except Exception as exc:
                    logger.debug("Post-click verification failed: %s", exc)
            return confirm

        return str(ActionError(
            code=ActionErrorCode.STALE_SNAPSHOT,
            message=f"Ref '{ref}' not found or element is gone.",
            hint="Take a new snapshot (action='snapshot') to get fresh refs.",
            retry_strategy=RETRY_RE_READ,
        ))

    # CSS selector path
    is_css = bool(re.search(r'[#\.\[\]>:=~^$*]', target))
    if is_css:
        try:
            await backend.click(target)
            confirm = f"Clicked: {target}"
            if await snapshot_mgr.is_stale(backend):
                snapshot = await snapshot_mgr.take_snapshot(backend)
                return f"{confirm}\n\n{snapshot_mgr.format_snapshot(snapshot)}"
            return confirm
        except (ValueError, Exception):
            return await element_not_found_hint(backend, target)

    # Natural description path
    clicked = await backend.click_by_role(target)
    if not clicked:
        try:
            await backend.click(target)
            return f"Clicked: {target}"
        except (ValueError, Exception):
            return await element_not_found_hint(backend, target)

    await asyncio.sleep(0.5)
    confirm = f"Clicked: {clicked['role']} \"{clicked['name']}\""
    if await snapshot_mgr.is_stale(backend):
        snapshot = await snapshot_mgr.take_snapshot(backend)
        return f"{confirm}\n\n{snapshot_mgr.format_snapshot(snapshot)}"
    return confirm


async def action_type(
    user_id: str, params: dict, tab_context, snapshot_mgr, verifier,
) -> str:
    """Type text into an element by ref ID, CSS selector, or natural description."""
    ref = (params.get("ref") or "").strip()
    target = (params.get("target") or "").strip()
    text = params.get("text", "")
    if (not ref and not target) or not text:
        return str(ActionError(
            code=ActionErrorCode.POLICY_DENIED,
            message="type requires ref (or target) AND text.",
            hint="Example: action='type', ref='e5', text='hello'",
            retry_strategy=RETRY_GIVE_UP,
        ))

    backend = await get_backend(user_id, tab_context)

    # Ref-ID path
    if ref:
        _before_state = None
        try:
            _before_state = await capture_state(backend, snapshot_mgr, target_ref=ref)
        except Exception as exc:
            logger.debug("Failed to capture pre-type state: %s", exc)
        focused = await snapshot_mgr.focus_ref(backend, ref)
        if focused:
            await human_type_text(backend, text)
            meta = await snapshot_mgr.get_ref_meta(backend, ref)
            name = meta.get("name", ref) if meta else ref
            confirm = f"Typed '{text[:30]}' into [{ref}] \"{name}\""
            if await snapshot_mgr.is_stale(backend):
                snapshot = await snapshot_mgr.take_snapshot(backend)
                confirm = f"{confirm}\n\n{snapshot_mgr.format_snapshot(snapshot)}"
            if _before_state is not None:
                try:
                    _error_text = await capture_error_text(backend)
                    _after_state = await capture_state_fresh(backend, snapshot_mgr, target_ref=ref)
                    _vr = verifier.verify(
                        _before_state, _after_state, "type",
                        target_ref=ref, error_text=_error_text,
                    )
                    confirm += f"\n{_vr.format(f'Typed into [{ref}]')}"
                except Exception as exc:
                    logger.debug("Post-type verification failed: %s", exc)
            return confirm
        return str(ActionError(
            code=ActionErrorCode.STALE_SNAPSHOT,
            message=f"Ref '{ref}' not found or couldn't focus.",
            hint="Take a new snapshot to get fresh refs, or scroll the element into view.",
            retry_strategy=RETRY_RE_READ,
        ))

    # CSS selector path
    is_css = bool(re.search(r'[#\.\[\]>:=~^$*]', target))
    if is_css:
        await backend.type_text(target, text)
        confirm = f"Typed '{text[:30]}...' into {target}"
        if await snapshot_mgr.is_stale(backend):
            snapshot = await snapshot_mgr.take_snapshot(backend)
            return f"{confirm}\n\n{snapshot_mgr.format_snapshot(snapshot)}"
        return confirm

    # Natural description path
    match = await backend.find_element_by_role(target)
    if not match:
        return str(ActionError(
            code=ActionErrorCode.NOT_FOUND,
            message=f"No element found matching '{target}'.",
            hint="Try a CSS selector, or action='snapshot' to see refs.",
            retry_strategy=RETRY_RE_READ,
        ))

    # human_type clicks the field first (bezier approach), then types.
    await human_type_text(
        backend, text, field_x=match["x"], field_y=match["y"],
    )
    confirm = f"Typed '{text[:30]}...' into {match['role']} \"{match['name']}\""
    if await snapshot_mgr.is_stale(backend):
        snapshot = await snapshot_mgr.take_snapshot(backend)
        return f"{confirm}\n\n{snapshot_mgr.format_snapshot(snapshot)}"
    return confirm


async def action_press_key(
    user_id: str, params: dict, tab_context, snapshot_mgr, verifier,
) -> str:
    """Press a keyboard key (Enter, Escape, Tab, etc)."""
    key = (params.get("target") or params.get("text") or "").strip()
    if not key:
        return str(ActionError(
            code=ActionErrorCode.POLICY_DENIED,
            message="press_key requires a key name.",
            hint="Example: action='press_key', target='Enter' (also Escape, Tab, Backspace, ArrowDown).",
            retry_strategy=RETRY_GIVE_UP,
        ))
    backend = await get_backend(user_id, tab_context)
    _before_state = None
    try:
        _before_state = await capture_state(backend, snapshot_mgr)
    except Exception as exc:
        logger.debug("Failed to capture pre-key-press state: %s", exc)
    await backend.press_key(key)
    result = f"Pressed: {key}"
    if _before_state is not None:
        try:
            await asyncio.sleep(0.3)
            _error_text = await capture_error_text(backend)
            _after_state = await capture_state_fresh(backend, snapshot_mgr)
            _vr = verifier.verify(
                _before_state, _after_state, "press_key", error_text=_error_text,
            )
            result += f"\n{_vr.format(f'Pressed {key}')}"
        except Exception as exc:
            logger.debug("Post-key-press verification failed: %s", exc)
    return result


async def action_hover(user_id: str, params: dict, tab_context) -> str:
    """Hover over an element."""
    target = (params.get("target") or "").strip()
    if not target:
        return str(ActionError(
            code=ActionErrorCode.POLICY_DENIED,
            message="hover requires a target (CSS selector).",
            hint="Example: action='hover', target='.menu-item'",
            retry_strategy=RETRY_GIVE_UP,
        ))
    backend = await get_backend(user_id, tab_context)
    try:
        await backend.hover(target)
    except ValueError:
        return str(ActionError(
            code=ActionErrorCode.NOT_FOUND,
            message=f"Hover target not found: {target}",
            hint="Take a fresh snapshot or try a different selector.",
            retry_strategy=RETRY_RE_READ,
        ))
    return f"Hovering over: {target}"


async def action_drag(user_id: str, params: dict, tab_context) -> str:
    """Drag element from source to destination."""
    source = (params.get("target") or "").strip()
    dest = (params.get("destination") or "").strip()
    if not source or not dest:
        return str(ActionError(
            code=ActionErrorCode.POLICY_DENIED,
            message="drag requires both target (source) and destination.",
            hint="Example: action='drag', target='.card-1', destination='.column-2'",
            retry_strategy=RETRY_GIVE_UP,
        ))
    backend = await get_backend(user_id, tab_context)
    try:
        await backend.drag_and_drop(source, dest)
    except ValueError:
        return str(ActionError(
            code=ActionErrorCode.NOT_FOUND,
            message="Source or destination element not found for drag.",
            hint="Take a fresh snapshot, check both selectors, and retry.",
            retry_strategy=RETRY_RE_READ,
        ))
    return f"Dragged {source} -> {dest}"
