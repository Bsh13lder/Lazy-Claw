"""Pre-plan research pass — gather context BEFORE the plan LLM call.

Four parallel lanes, zero brain LLM calls, hard 2-second wallclock cap each:
  1. semantic_search over LazyBrain (top 3 notes)
  2. recall_skill_lessons for matched topic shapes (top 2)
  3. optional codebase grep — only when message references a path-like or
     "in <file>" target. Skipped for general queries.
  4. project-tasks lane — groups the user's active tasks by inferred
     project (`category` field) and surfaces the top buckets that overlap
     with the current message tokens. No `project_id` column needed —
     `category` already acts as the project handle.

Returns ``''`` if no findings, else a ``"## Research findings\\n..."`` block.
Failure of any lane is non-fatal — partial results still make the plan
better than zero context. Worker-LLM only via embeddings; brain is never
called here.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shlex

logger = logging.getLogger(__name__)


_LANE_TIMEOUT_S: float = 2.0

# Topic keywords reused from context_builder (single source of truth).
# We keep a copy locally to avoid import cycles — context_builder also
# imports from runtime, and pulling _TOPIC_KEYWORDS through would re-enter.
_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "n8n":       ("n8n", "workflow", "webhook"),
    "instagram": ("instagram", "insta", "reel", " ig "),
    "email":     ("email", "gmail", "inbox", "imap"),
    "whatsapp":  ("whatsapp", "wa msg", "whats app"),
    "browser":   ("browser", "open ", "click ", "navigate ", "screenshot",
                  "scroll", " url ", "tab "),
    "web":       ("search ", "google ", "duckduckgo", "lookup ", "find info",
                  "what is ", "who is ", "latest news"),
    "telegram":  ("telegram", " tg "),
    "lazybrain": ("note", "wikilink", "graph", "lazybrain"),
    "tasks":     ("task", "remind", "todo", "background job"),
    "vault":     ("api key", "credential", "secret ", "vault"),
}


_PATH_LIKE_RE = re.compile(
    r"(?:[\w./-]+/[\w./-]+|\b[\w-]+\.(?:py|ts|tsx|js|jsx|md|json|yaml|yml|html|css|sql))",
)
_IN_FILE_RE = re.compile(r"\bin\s+`?([\w./-]+\.[a-zA-Z]{1,5})`?", re.IGNORECASE)


def _matched_topics(message: str) -> list[str]:
    """Top-2 keyword-scored topics from the message."""
    hay = f" {message.lower()} "
    scored: list[tuple[int, str]] = [
        (sum(1 for kw in kws if kw in hay), topic)
        for topic, kws in _TOPIC_KEYWORDS.items()
    ]
    scored = [(s, t) for s, t in scored if s > 0]
    scored.sort(reverse=True)
    return [t for _, t in scored[:2]]


def _file_targets(message: str) -> list[str]:
    """Extract path-like or 'in <file>' targets from the message.

    Cheap heuristic — only looks like a path if it has a slash or a
    common code extension. Returns at most 3 candidates.
    """
    found: list[str] = []
    for m in _IN_FILE_RE.finditer(message):
        token = m.group(1).strip()
        if token and token not in found:
            found.append(token)
    for m in _PATH_LIKE_RE.finditer(message):
        token = m.group(0).strip()
        if token and token not in found:
            found.append(token)
        if len(found) >= 3:
            break
    return found[:3]


async def _lane_lazybrain(config, user_id: str, query: str) -> list[str]:
    """LazyBrain semantic search — top 3 hits as bullet lines."""
    try:
        from lazyclaw.lazybrain.embeddings import semantic_search

        hit = await semantic_search(
            config, user_id, query, k=3, min_similarity=0.0,
        )
        results = hit.get("results") or []
        out: list[str] = []
        for n in results[:3]:
            title = (n.get("title") or "(untitled)").strip()
            snippet = (n.get("content") or "").strip().splitlines()[0][:200]
            out.append(f"- **{title}** — {snippet}")
        return out
    except Exception:
        logger.debug("plan_research lane_lazybrain failed", exc_info=True)
        return []


async def _lane_lessons(config, user_id: str, message: str) -> list[str]:
    """recall_skill_lessons for top-2 matched topics, k=2 each."""
    topics = _matched_topics(message)
    if not topics:
        return []
    try:
        from lazyclaw.runtime.skill_lesson import recall_skill_lessons
    except Exception:
        return []

    out: list[str] = []
    for topic in topics:
        try:
            hits = await recall_skill_lessons(
                config, user_id, topic=topic, intent=message, k=2,
            )
        except Exception:
            continue
        for h in hits[:2]:
            title = (h.get("title") or "").strip()
            action = (h.get("action") or "").strip()
            line = f"- _{topic}_: **{title}** → `{action}`" if action else f"- _{topic}_: **{title}**"
            out.append(line[:300])
    return out


async def _lane_codebase(message: str) -> list[str]:
    """Optional codebase grep — only fires when message references a file.

    Uses ``grep -rn`` capped to 200ms-ish (process is forked synchronously
    via asyncio subprocess; the outer ``wait_for(_LANE_TIMEOUT_S)`` enforces
    the wall-clock cap).
    """
    targets = _file_targets(message)
    if not targets:
        return []

    out: list[str] = []
    for target in targets[:2]:
        # Look up the file: existence check, then a one-liner summary.
        # Path is shell-quoted to avoid argument injection from messages.
        clean = shlex.quote(target)
        cmd = (
            f"if [ -f {clean} ]; then "
            f"echo \"FILE {target} ($(wc -l < {clean} | tr -d ' ') lines)\"; "
            f"head -3 {clean}; "
            f"else "
            f"grep -rln --include='*.py' --include='*.ts' --include='*.tsx' "
            f"-- {clean} . 2>/dev/null | head -3; "
            f"fi"
        )
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            text = (stdout or b"").decode("utf-8", errors="replace").strip()
            if text:
                out.append(f"- `{target}` →\n```\n{text[:500]}\n```")
        except Exception:
            logger.debug("plan_research codebase grep failed", exc_info=True)
            continue
    return out


async def _lane_project_tasks(config, user_id: str, message: str) -> list[str]:
    """Group active tasks by inferred project (``category``) and surface
    the top 2 buckets that share tokens with the current message.

    Reuses the same tokenizer the context_builder uses for hybrid memory
    so "what's left in lazyclaw?" lands the right bucket without an LLM
    call. Returns at most 2 projects × 3 tasks each. Empty list when the
    user has no active tasks or no overlap with the message.
    """
    try:
        from lazyclaw.tasks.store import list_tasks
        from lazyclaw.runtime.context_builder import _tokenize_for_memory
    except Exception:
        logger.debug("plan_research lane_project_tasks imports failed", exc_info=True)
        return []

    try:
        active = await list_tasks(
            config, user_id, status="todo", owner="user",
        )
    except Exception:
        logger.debug("plan_research lane_project_tasks list failed", exc_info=True)
        return []

    if not active:
        return []

    # Bucket by category. Tasks without a category fall into a single
    # "(uncategorised)" group so the user still sees them when the message
    # has no project anchor — they just won't outrank a real project.
    buckets: dict[str, list[dict]] = {}
    for t in active:
        cat = (t.get("category") or "").strip().lower() or "(uncategorised)"
        buckets.setdefault(cat, []).append(t)

    msg_tokens = _tokenize_for_memory(message)

    # Score each bucket by message-token overlap with the bucket's own
    # name + each task's title. Buckets with zero overlap rank by size so
    # the user always sees their biggest active project.
    scored: list[tuple[int, int, str, list[dict]]] = []
    for cat, tasks in buckets.items():
        cat_tokens = _tokenize_for_memory(cat)
        overlap = len(msg_tokens & cat_tokens)
        for task in tasks:
            overlap += len(
                msg_tokens & _tokenize_for_memory(task.get("title") or "")
            )
        # Sort key: high overlap first; among same-overlap, larger bucket first.
        scored.append((-overlap, -len(tasks), cat, tasks))

    scored.sort()

    # Drop the catch-all bucket from the displayed top unless it's the
    # only thing the user has — it isn't a project.
    top: list[tuple[str, list[dict]]] = []
    for _, _, cat, tasks in scored:
        if cat == "(uncategorised)" and len(top) > 0:
            continue
        top.append((cat, tasks))
        if len(top) >= 2:
            break

    out: list[str] = []
    for cat, tasks in top:
        out.append(f"- **{cat}** ({len(tasks)} active):")
        for t in tasks[:3]:
            title = (t.get("title") or "(untitled)").strip()[:80]
            prio = t.get("priority") or "medium"
            due = t.get("due_date") or "no due date"
            out.append(f"  - {title}  _(priority: {prio}, due: {due})_")
    return out


async def _run_with_timeout(coro, lane_name: str) -> list[str]:
    """Wrap a lane in a hard 2-second wall-clock cap. Failures yield []."""
    try:
        return await asyncio.wait_for(coro, timeout=_LANE_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.debug("plan_research lane %s timed out", lane_name)
        return []
    except Exception:
        logger.debug("plan_research lane %s errored", lane_name, exc_info=True)
        return []


async def gather_plan_research(config, user_id: str, message: str) -> str:
    """Top-level entry. Returns a markdown block or empty string.

    Runs all three lanes in parallel, applies a hard timeout per lane, and
    formats the surviving results into a single ``## Research findings``
    section ready to inject into the plan prompt.
    """
    msg = (message or "").strip()
    if not msg:
        return ""

    lazybrain_co = _run_with_timeout(_lane_lazybrain(config, user_id, msg), "lazybrain")
    lessons_co = _run_with_timeout(_lane_lessons(config, user_id, msg), "lessons")
    codebase_co = _run_with_timeout(_lane_codebase(msg), "codebase")
    project_co = _run_with_timeout(
        _lane_project_tasks(config, user_id, msg), "project_tasks",
    )

    lazybrain, lessons, codebase, projects = await asyncio.gather(
        lazybrain_co, lessons_co, codebase_co, project_co,
    )

    parts: list[str] = []
    if lazybrain:
        parts.append("### LazyBrain notes")
        parts.extend(lazybrain)
    if lessons:
        parts.append("### Past working shapes")
        parts.extend(lessons)
    if codebase:
        parts.append("### Codebase references")
        parts.extend(codebase)
    if projects:
        parts.append("### Other tasks in this project")
        parts.extend(projects)

    if not parts:
        return ""

    return "## Research findings\n" + "\n".join(parts)
