"""Synthesize a browser template draft from the live event stream.

One shared helper used by three callers:
  - save_browser_template NL skill (user says "save this as template X")
  - /api/browser/templates/from-current-session (canvas "Save as template" button)
  - chat_ws post-turn hook (auto-suggest after multi-step flows)

Reuses the zero-token event bus ring buffer — the flow the user just ran is
already sitting there. We distil setup_urls + checkpoints from event metadata
and let the LLM worker draft a short playbook narrative.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from lazyclaw.browser import event_bus

if TYPE_CHECKING:
    from lazyclaw.config import Config
    from lazyclaw.llm.router import LLMRouter

logger = logging.getLogger(__name__)

_MAX_SETUP_URLS = 5
_MAX_EVENTS = 30
_DEFAULT_WINDOW_S = 600


# Cheap host→icon hints. Everything else falls back to the compass.
_ICON_HINTS: tuple[tuple[str, str], ...] = (
    ("dgt.gob.es", "🚗"),
    ("administracionespublicas.gob.es", "📋"),
    ("seg-social", "🏥"),
    ("sanidad", "🏥"),
    ("doctoralia", "🩺"),
    ("gmail", "📧"),
    ("outlook", "📧"),
    ("github", "🐙"),
    ("amazon", "🛒"),
    ("booking", "🏨"),
    ("airbnb", "🏨"),
    ("upwork", "💼"),
    ("linkedin", "💼"),
    ("whatsapp", "💬"),
    ("instagram", "📷"),
    ("maps.google", "🗺"),
)


@dataclass(frozen=True)
class TemplateDraft:
    """Immutable draft — caller decides whether to persist it."""

    name: str
    setup_urls: list[str]
    checkpoints: list[str]
    playbook: str
    icon: str
    event_count: int

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "setup_urls": list(self.setup_urls),
            "checkpoints": list(self.checkpoints),
            "playbook": self.playbook,
            "icon": self.icon,
            "event_count": self.event_count,
        }


def _pick_icon(setup_urls: list[str]) -> str:
    for u in setup_urls:
        try:
            host = urlparse(u).netloc.lower()
        except ValueError:
            continue
        for needle, icon in _ICON_HINTS:
            if needle in host:
                return icon
    return "🧭"


def _extract_setup_urls(events) -> list[str]:
    seen: dict[str, None] = {}
    for e in events:
        action = getattr(e, "action", None)
        url = getattr(e, "url", None)
        if not url:
            continue
        if action == "goto" or (action is None and e.kind == "navigate"):
            if url not in seen:
                seen[url] = None
        if len(seen) >= _MAX_SETUP_URLS:
            break
    if not seen:
        # No explicit goto — fall back to the first URL we saw at all.
        for e in events:
            if getattr(e, "url", None):
                seen[e.url] = None
                break
    return list(seen.keys())


def _extract_checkpoints(events) -> list[str]:
    seen: dict[str, None] = {}
    for e in events:
        if e.kind != "checkpoint":
            continue
        extra = getattr(e, "extra", None) or {}
        resolved = extra.get("resolved") if isinstance(extra, dict) else None
        if resolved in ("rejected",):
            continue
        label = getattr(e, "target", None) or getattr(e, "detail", None)
        if label and label not in seen:
            seen[label] = None
    return list(seen.keys())


def _suggest_name_from_events(events) -> str:
    title = None
    host = None
    for e in reversed(events):
        if getattr(e, "title", None) and not title:
            title = e.title
        if getattr(e, "url", None) and not host:
            try:
                host = urlparse(e.url).netloc.replace("www.", "")
            except ValueError:
                host = None
        if title and host:
            break
    if title:
        return title[:60].strip()
    if host:
        return host[:60]
    return "Untitled flow"


def _compact_timeline(events) -> str:
    lines: list[str] = []
    for e in events:
        action = getattr(e, "action", None) or e.kind
        target = getattr(e, "target", None) or ""
        detail = getattr(e, "detail", None) or ""
        url = getattr(e, "url", None) or ""
        # Prefer human-readable detail when present, else action + target.
        if detail:
            line = f"- {action}: {detail}"
        elif target:
            line = f"- {action}: {target}"
        else:
            line = f"- {action}"
        if url and action == "goto":
            line += f" ({url})"
        lines.append(line)
    return "\n".join(lines[:_MAX_EVENTS])


_PLAYBOOK_PROMPT = """You draft short playbooks for a browser automation agent.

Below is a real sequence of browser actions the user just ran. Write a 3-6 line
playbook the agent can follow to repeat this flow next time. Keep it practical:
say what the flow DOES, mention any obvious vault keys needed (e.g. NIE, email,
password), note any approval checkpoints by name, and flag quirks visible in
the actions. Do NOT invent steps that aren't in the timeline.

Flow name: {name}

Timeline:
{timeline}

Approval checkpoints seen: {checkpoints}

Write the playbook (plain text, no markdown fences):"""


async def _draft_playbook(
    router: LLMRouter,
    config: Config,
    user_id: str,
    name: str,
    timeline: str,
    checkpoints: list[str],
) -> str:
    """One cheap LLM call on the worker model. Fire-and-forget safe."""
    try:
        from lazyclaw.llm.providers.base import LLMMessage

        prompt = _PLAYBOOK_PROMPT.format(
            name=name,
            timeline=timeline or "(no actions captured)",
            checkpoints=", ".join(checkpoints) if checkpoints else "(none)",
        )
        messages = [
            LLMMessage(role="system", content="You write short, literal playbooks for a browser agent."),
            LLMMessage(role="user", content=prompt),
        ]
        model = getattr(config, "worker_model", None)
        response = await router.chat(messages, model=model, user_id=user_id)
        text = (response.content or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return text[:1500]
    except Exception:
        logger.warning("Playbook drafting failed — falling back to timeline", exc_info=True)
        return f"Auto-captured flow '{name}'. Steps:\n{timeline}"


async def synthesize_template_from_events(
    config: Config,
    router: LLMRouter,
    user_id: str,
    name: str | None = None,
    *,
    window_s: float = _DEFAULT_WINDOW_S,
    since_ts: float | None = None,
) -> TemplateDraft | None:
    """Build a TemplateDraft from the user's recent browser activity.

    Returns None if there's nothing usable (no events in window).
    `since_ts` takes precedence over `window_s` when set — used by the
    auto-suggest hook to scope strictly to the current agent turn.
    """
    if since_ts is not None:
        max_age = max(1.0, time.time() - since_ts)
    else:
        max_age = window_s
    events = event_bus.recent_events(user_id, limit=_MAX_EVENTS, max_age_s=max_age)
    if not events:
        return None

    setup_urls = _extract_setup_urls(events)
    checkpoints = _extract_checkpoints(events)
    if not setup_urls and not checkpoints:
        # Nothing recognizable — no point making a template.
        return None

    final_name = (name or "").strip() or _suggest_name_from_events(events)
    icon = _pick_icon(setup_urls)
    timeline = _compact_timeline(events)
    playbook = await _draft_playbook(
        router, config, user_id, final_name, timeline, checkpoints,
    )

    return TemplateDraft(
        name=final_name,
        setup_urls=setup_urls,
        checkpoints=checkpoints,
        playbook=playbook,
        icon=icon,
        event_count=len(events),
    )


async def draft_template_from_prompt(
    router: LLMRouter,
    config: Config,
    user_id: str,
    prompt: str,
) -> dict:
    """Build a NON-persisted draft from a natural-language description.

    Used by the '✨ Create with AI' dialog on the Templates page. The draft
    is returned to the frontend so the user can review/edit in the existing
    form before saving — we never silently write to the DB here.
    """
    from lazyclaw.llm.providers.base import LLMMessage

    system = (
        "You turn one-line descriptions of browser automation goals into "
        "a structured template draft. Output valid JSON only — no markdown, "
        "no prose. Schema: "
        "{\"name\": str, \"icon\": str (one emoji), "
        "\"setup_urls\": list[str] (1-3 real URLs), "
        "\"playbook\": str (3-6 lines, plain text), "
        "\"checkpoints\": list[str] (approval step names)}. "
        "Only use real URLs you are confident exist. When unsure, leave setup_urls empty."
    )
    user_msg = f"User's goal: {prompt.strip()}\n\nReturn JSON."
    messages = [
        LLMMessage(role="system", content=system),
        LLMMessage(role="user", content=user_msg),
    ]
    model = getattr(config, "worker_model", None)
    try:
        response = await router.chat(messages, model=model, user_id=user_id)
    except Exception as exc:
        logger.warning("from-prompt LLM call failed: %s", exc, exc_info=True)
        raise

    raw = (response.content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    import json
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("from-prompt response was not valid JSON: %s", raw[:200])
        return {
            "name": prompt.strip()[:60],
            "icon": "🧭",
            "setup_urls": [],
            "playbook": raw[:1500],
            "checkpoints": [],
        }

    return {
        "name": str(data.get("name") or prompt.strip()[:60]),
        "icon": str(data.get("icon") or "🧭")[:4],
        "setup_urls": [str(u) for u in (data.get("setup_urls") or []) if u][:_MAX_SETUP_URLS],
        "playbook": str(data.get("playbook") or "")[:1500],
        "checkpoints": [str(c) for c in (data.get("checkpoints") or []) if c][:10],
    }
