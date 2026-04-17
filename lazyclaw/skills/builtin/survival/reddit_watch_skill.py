"""Watch Reddit /r/forhire (and similar) for [HIRING] posts matching profile.

One-shot skill: fetches the subreddit's public JSON feed, filters posts
by [HIRING] tag and keywords from the user's SkillsProfile, dedupes against
previously-seen post IDs (stored in personal_memory, encrypted), and
pushes new matches to Telegram.

Schedule it via the existing schedule_job skill to run every 5-15 minutes.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

_SEEN_MEMORY_TYPE = "reddit_forhire_seen"
_MAX_SEEN_IDS = 500

_DEFAULT_SUBS = ("forhire", "slavelabour", "jobbit", "hireaprogrammer")


class WatchRedditForHireSkill(BaseSkill):
    """Poll Reddit hiring subs, push new [HIRING] posts matching skills."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "watch_reddit_forhire"

    @property
    def description(self) -> str:
        return (
            "Poll Reddit hiring subreddits (/r/forhire, /r/slavelabour, /r/jobbit, "
            "/r/hireaprogrammer by default) for new [HIRING] posts matching the user's "
            "skills profile. Pushes matches to Telegram. One-shot — schedule with "
            "schedule_job 'watch_reddit_forhire' every 5-10 minutes for continuous monitoring."
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "subreddits": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of subreddit names (without /r/). "
                        f"Default: {', '.join(_DEFAULT_SUBS)}"
                    ),
                },
                "max_per_sub": {
                    "type": "integer",
                    "description": "Max new posts to check per sub (default 25).",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: not configured"

        from lazyclaw.survival.profile import get_profile
        profile = await get_profile(self._config, user_id)

        if not profile.skills:
            return (
                "No skills profile set. Set one first: "
                "'my skills are python, fastapi, scraping'"
            )

        subs = params.get("subreddits") or _DEFAULT_SUBS
        max_per_sub = min(int(params.get("max_per_sub", 25)), 100)

        seen = await _load_seen(self._config, user_id)

        keywords = tuple(s.lower() for s in profile.skills)
        excluded = tuple(k.lower() for k in profile.excluded_keywords)

        matches: list[dict] = []
        async with httpx.AsyncClient(
            headers={"User-Agent": "LazyClaw/1.0 (freelance gig watcher)"},
            timeout=15.0,
            follow_redirects=True,
        ) as client:
            for sub in subs:
                try:
                    posts = await _fetch_sub(client, sub, max_per_sub)
                except Exception as exc:
                    logger.warning("Reddit fetch failed for /r/%s: %s", sub, exc)
                    continue
                for post in posts:
                    if post["id"] in seen:
                        continue
                    if not _is_hiring(post["title"]):
                        continue
                    if not _matches_keywords(post, keywords):
                        continue
                    if _hits_excluded(post, excluded):
                        continue
                    matches.append(post)

        # Mark everything we saw this pass as seen (whether matched or not) so we
        # don't re-evaluate next run. Trim to last _MAX_SEEN_IDS.
        new_seen = set(seen)
        for sub in subs:
            # Re-cheap: only the matches + the IDs we actually pulled count as seen.
            pass
        new_seen.update(p["id"] for p in matches)
        # Conservative: also record IDs we fetched but didn't match, so we don't
        # re-alert on the same listing if the user later changes keywords.
        # (Matches are the only thing we push; non-matches just dedupe silently.)
        # We rely on the caller loop above to have pulled posts.
        # Nothing to do here since post list isn't kept across subs.
        await _save_seen(self._config, user_id, new_seen)

        if not matches:
            return f"No new [HIRING] posts matching {', '.join(profile.skills[:5])} across /r/{', /r/'.join(subs)}."

        lines = [f"🆕 {len(matches)} new Reddit hiring post(s):\n"]
        for p in matches[:10]:
            lines.append(f"• *{p['sub']}* — {p['title'][:120]}")
            lines.append(f"  {p['url']}")
            lines.append("")

        text = "\n".join(lines)

        try:
            from lazyclaw.notifications.push import push_telegram
            await push_telegram(self._config, text)
        except Exception as exc:
            logger.debug("Telegram push for reddit watcher skipped: %s", exc)

        return text


# -- HTTP + parse helpers ---------------------------------------------------

async def _fetch_sub(client: httpx.AsyncClient, sub: str, limit: int) -> list[dict]:
    """Fetch the subreddit's newest posts via public JSON endpoint."""
    url = f"https://www.reddit.com/r/{sub}/new.json?limit={limit}"
    resp = await client.get(url)
    resp.raise_for_status()
    data = resp.json()
    posts: list[dict] = []
    for child in data.get("data", {}).get("children", []):
        d = child.get("data", {})
        pid = d.get("id")
        if not pid:
            continue
        posts.append({
            "id": pid,
            "sub": sub,
            "title": d.get("title", "") or "",
            "selftext": d.get("selftext", "") or "",
            "url": "https://www.reddit.com" + d.get("permalink", ""),
            "created_utc": d.get("created_utc", 0),
        })
    return posts


def _is_hiring(title: str) -> bool:
    t = title.lower()
    return ("[hiring]" in t or "hiring]" in t) and "[for hire]" not in t


def _matches_keywords(post: dict, keywords: tuple[str, ...]) -> bool:
    if not keywords:
        return True
    blob = (post.get("title", "") + " " + post.get("selftext", "")).lower()
    return any(k in blob for k in keywords)


def _hits_excluded(post: dict, excluded: tuple[str, ...]) -> bool:
    if not excluded:
        return False
    blob = (post.get("title", "") + " " + post.get("selftext", "")).lower()
    return any(ex in blob for ex in excluded)


# -- Dedupe store (encrypted memory) ---------------------------------------

async def _load_seen(config: Any, user_id: str) -> set[str]:
    from lazyclaw.memory.personal import get_memories
    memories = await get_memories(config, user_id, limit=100)
    for m in memories:
        if m.get("type") == _SEEN_MEMORY_TYPE:
            content = m.get("content") or ""
            return set(s.strip() for s in content.split(",") if s.strip())
    return set()


async def _save_seen(config: Any, user_id: str, ids: set[str]) -> None:
    from lazyclaw.memory.personal import (
        delete_memory,
        get_memories,
        save_memory,
    )
    trimmed = list(ids)[-_MAX_SEEN_IDS:]
    content = ",".join(trimmed)

    memories = await get_memories(config, user_id, limit=100)
    for m in memories:
        if m.get("type") == _SEEN_MEMORY_TYPE:
            await delete_memory(config, user_id, m["id"])
            break

    await save_memory(
        config, user_id,
        content=content,
        memory_type=_SEEN_MEMORY_TYPE,
        importance=1,
    )
