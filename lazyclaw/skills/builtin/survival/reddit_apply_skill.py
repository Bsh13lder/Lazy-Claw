"""apply_reddit_dm — send a Reddit DM via the user's logged-in Brave.

Reads the user's logged-in Reddit session through LazyClaw's CDP browser.
Drives Reddit's `/message/compose` URL (which still pre-fills `to`,
`subject`, `message` query params even after Aug 2025 when Reddit
rerouted those messages through Reddit Chat).

Flow:
  1. Resolve recipient — accept either `post_url` (we fetch the JSON to
     get the author) or `username` directly.
  2. Draft the DM body if not provided.
  3. Open the compose URL in the user's Brave so the form is pre-filled.
  4. Fire a `request_user_approval` checkpoint — user reviews on the
     BrowserCanvas / Telegram and clicks Approve before send.
  5. On approve, click the Send button.

Cookies persist across sessions via the shared profile dir
(`browser_profiles/{user_id}/`), so the user logs into Reddit once.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus, urlparse

import httpx

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

_COMPOSE_BASE = "https://www.reddit.com/message/compose/"
_HTTP_TIMEOUT = 15.0
_REDDIT_HEADERS = {"User-Agent": "LazyClaw/1.0 (gig outreach)"}

# Match /u/foo, u/foo, or bare foo. Reddit usernames are 3-20 chars,
# alphanumeric + underscore + hyphen.
_USERNAME_CLEAN_RE = re.compile(r"^/?u/", re.IGNORECASE)
_USERNAME_VALID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,20}$")

# Reddit post URLs look like:
#   https://www.reddit.com/r/forhire/comments/1abcdef/title_slug/
#   https://reddit.com/r/forhire/comments/1abcdef/
#   https://old.reddit.com/r/forhire/comments/1abcdef/title/
_POST_URL_RE = re.compile(
    r"https?://(?:www\.|old\.|new\.)?reddit\.com/r/[^/]+/comments/([a-z0-9]+)",
    re.IGNORECASE,
)


def _clean_username(raw: str) -> str:
    """Strip /u/ or u/ prefix and validate."""
    if not raw:
        return ""
    cleaned = _USERNAME_CLEAN_RE.sub("", raw.strip())
    return cleaned if _USERNAME_VALID_RE.match(cleaned) else ""


def _build_compose_url(to: str, subject: str, message: str) -> str:
    """Build the Reddit compose URL with pre-filled params.

    URL-encodes each param so emoji / newlines / special chars survive.
    """
    return (
        f"{_COMPOSE_BASE}?to={quote_plus(to)}"
        f"&subject={quote_plus(subject)}"
        f"&message={quote_plus(message)}"
    )


def _post_id_from_url(url: str) -> str | None:
    """Extract the Reddit post ID (1abcdef) from a post URL."""
    m = _POST_URL_RE.search(url or "")
    return m.group(1) if m else None


async def _fetch_post_author(post_url: str) -> tuple[str, str]:
    """Fetch a Reddit post's JSON to get (author_username, post_title).

    Returns ("", "") if the post is unavailable, deleted, or the URL
    can't be parsed. Uses the public .json endpoint — zero auth needed.
    """
    if not post_url:
        return "", ""

    parsed = urlparse(post_url)
    if not parsed.netloc.endswith("reddit.com"):
        return "", ""

    # Strip trailing slash + query, append .json
    json_url = post_url.split("?")[0].rstrip("/") + ".json"

    try:
        async with httpx.AsyncClient(
            headers=_REDDIT_HEADERS,
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = await client.get(json_url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Failed to fetch Reddit post JSON %s: %s", post_url, exc)
        return "", ""

    # Reddit returns [post_listing, comments_listing]; the post is at [0]
    try:
        post_data = data[0]["data"]["children"][0]["data"]
    except (IndexError, KeyError, TypeError):
        return "", ""

    author = post_data.get("author") or ""
    title = post_data.get("title") or ""
    if author == "[deleted]":
        author = ""
    return author, title


_LAZYCLAW_BRANDING_HINT = (
    "Mention you're an AI-augmented developer (LazyClaw open-source agent) "
    "with human review of every deliverable. Frame AI as faster turnaround "
    "and a real human accountable for quality. Brief, no marketing fluff."
)


class ApplyRedditDmSkill(BaseSkill):
    """Apply to a Reddit [HIRING] post by sending a DM via the user's Brave."""

    def __init__(self, config=None, registry=None) -> None:
        self._config = config
        self._registry = registry

    @property
    def name(self) -> str:
        return "apply_reddit_dm"

    @property
    def description(self) -> str:
        return (
            "Apply to a Reddit hiring post by sending a DM through the "
            "user's logged-in Brave session (cookies persist via shared "
            "profile). Pass `post_url` (we fetch the author + title) OR "
            "`username` directly. If `message` is omitted, drafts one "
            "from the user's skills profile. ALWAYS fires a "
            "request_user_approval checkpoint before sending — user must "
            "approve on the canvas. Usage examples: "
            "'DM the OP of <reddit-url> about my Python scraping services' "
            "or 'apply to reddit job 1 with rate $50'."
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "post_url": {
                    "type": "string",
                    "description": (
                        "Full Reddit post URL "
                        "(https://reddit.com/r/forhire/comments/...). "
                        "The author + title are auto-resolved via public JSON."
                    ),
                },
                "username": {
                    "type": "string",
                    "description": (
                        "Reddit username (with or without /u/ prefix). "
                        "Use this when you only have the username, not a post URL."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": (
                        "DM body. If omitted, a draft is generated from the "
                        "user's skills profile + post title."
                    ),
                },
                "subject": {
                    "type": "string",
                    "description": (
                        "DM subject line (max 100 chars). Default: "
                        "'re: <post title>' or 'Hello from LazyClaw'."
                    ),
                },
                "custom_note": {
                    "type": "string",
                    "description": (
                        "Optional user-supplied note appended to the draft, "
                        "e.g. price, turnaround, specific question."
                    ),
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        params = params or {}
        post_url = (params.get("post_url") or "").strip()
        username_raw = (params.get("username") or "").strip()
        message = (params.get("message") or "").strip()
        subject = (params.get("subject") or "").strip()
        custom_note = (params.get("custom_note") or "").strip()

        # 1. Resolve recipient + post title
        post_title = ""
        if post_url:
            author, post_title = await _fetch_post_author(post_url)
            username = _clean_username(author)
            if not username:
                return (
                    f"Could not resolve the post author from {post_url}. "
                    "Pass `username` explicitly or use a different URL."
                )
        else:
            username = _clean_username(username_raw)
            if not username:
                return (
                    "Need either `post_url` (we'll fetch the author) or a "
                    "valid `username` (3-20 chars, alphanumeric + _-)."
                )

        # 2. Draft the message if the caller didn't provide one
        if not message:
            message = await self._draft_message(
                user_id=user_id,
                username=username,
                post_url=post_url,
                post_title=post_title,
                custom_note=custom_note,
            )
        elif custom_note:
            message = f"{message}\n\n{custom_note}"

        if not subject:
            subject = (
                f"re: {post_title[:90]}"
                if post_title
                else "Re: your Reddit hiring post"
            )
        # Reddit caps subject at 100 chars
        subject = subject[:100]

        # 3. Open the pre-filled compose URL in the user's Brave
        compose_url = _build_compose_url(username, subject, message)
        browser = self._registry.get("browser") if self._registry else None
        if browser is None:
            # Fallback: hand the URL back so the user can paste it themselves
            return (
                f"Browser tool not available — open this URL manually:\n\n"
                f"{compose_url}\n\n"
                f"Draft message:\n{message}"
            )

        try:
            await browser.execute(user_id, {"action": "open", "url": compose_url})
            await browser.execute(user_id, {"action": "read"})
        except Exception as exc:
            logger.warning("browser.open(compose_url) failed: %s", exc)
            return (
                f"Browser couldn't open the compose page.\n"
                f"Open manually: {compose_url}\n\n"
                f"Draft:\n{message}"
            )

        # 4. Fire approval checkpoint — user reviews + decides on the canvas
        approval = self._registry.get("request_user_approval") if self._registry else None
        if approval is None:
            return (
                f"DM compose page opened in your Brave for u/{username}.\n"
                f"Subject: {subject}\n\n"
                f"---\n{message}\n---\n\n"
                f"Click Send manually if it looks good."
            )

        approval_result = await approval.execute(user_id, {
            "name": f"Send Reddit DM to u/{username}",
            "detail": (
                f"Recipient: u/{username}\n"
                f"Subject: {subject}\n\n"
                f"Message:\n{message}\n\n"
                f"Approve to send via your logged-in Brave."
            ),
            "timeout_seconds": 600,
        })

        if "rejected" in approval_result.lower():
            return (
                f"DM to u/{username} cancelled. The compose page is still "
                f"open in Brave — edit + send manually if you want."
            )

        # 5. Click Send. The button label varies between PM (Send) and the
        # post-Aug-2025 chat reroute (also Send), so a single target works.
        try:
            await browser.execute(user_id, {"action": "click", "target": "Send"})
        except Exception as exc:
            logger.warning("Could not click Send button: %s", exc)
            return (
                f"Approved but the Send click failed: {exc}.\n"
                f"The DM is composed and waiting in Brave — click Send manually."
            )

        return (
            f"DM sent to u/{username}.\n"
            f"Subject: {subject}\n"
            f"Body:\n---\n{message}\n---"
        )

    async def _draft_message(
        self,
        *,
        user_id: str,
        username: str,
        post_url: str,
        post_title: str,
        custom_note: str,
    ) -> str:
        """Draft a Reddit DM, preferring claude-code MCP for quality, falling
        back to a deterministic template if the LLM path is unavailable.
        """
        from lazyclaw.survival.profile import get_profile

        profile = await get_profile(self._config, user_id)
        skills_str = ", ".join(profile.skills[:5]) if profile.skills else "Python development"

        prompt = (
            f"Draft a short Reddit DM applying to a hiring post. Max 150 words. "
            f"Casual but professional tone — Reddit, not LinkedIn. No 'Dear Sir/Madam'. "
            f"Lead with one specific thing about the post that proves you read it.\n\n"
            f"Recipient: u/{username}\n"
            f"Post title: {post_title or 'a Reddit hiring post'}\n"
            f"Post URL: {post_url or '(not provided)'}\n\n"
            f"My profile:\n"
            f"Title: {profile.title or 'Python developer'}\n"
            f"Skills: {skills_str}\n"
            f"Bio: {profile.bio or 'AI-augmented developer for fast scripting and automation gigs.'}\n"
        )
        if custom_note:
            prompt += f"\nUser-supplied note (must include verbatim or paraphrase): {custom_note}\n"
        if profile.branding_mode == "lazyclaw":
            prompt += f"\n{_LAZYCLAW_BRANDING_HINT}\n"

        registry = self._registry
        if registry is not None:
            for tool_info in registry.list_mcp_tools():
                func = tool_info.get("function", {})
                tname = func.get("name", "").lower()
                if "claude" in tname and "code" in tname:
                    tool = registry.get(func.get("name", ""))
                    if tool is not None:
                        try:
                            return await tool.execute(user_id, {"prompt": prompt})
                        except Exception as exc:
                            logger.warning("claude-code DM draft failed: %s", exc)
                            break

        # Deterministic fallback — better than nothing, never broken
        return _fallback_draft(
            username=username,
            post_title=post_title,
            profile=profile,
            custom_note=custom_note,
        )


def _fallback_draft(*, username: str, post_title: str, profile, custom_note: str) -> str:
    """Template-based DM. Used when claude-code MCP isn't reachable."""
    skills_str = ", ".join(profile.skills[:3]) if profile.skills else "Python development"
    title_clip = f' "{post_title[:60]}"' if post_title else ""
    note_part = f"\n\n{custom_note}" if custom_note else ""

    if getattr(profile, "branding_mode", "") == "lazyclaw":
        return (
            f"Hi u/{username},\n\n"
            f"Saw your post{title_clip}. I work with {skills_str} and run "
            f"LazyClaw — an open-source AI agent stack I built. "
            f"Every deliverable is reviewed and tested by me before it ships. "
            f"That means faster turnaround for you with a real human "
            f"accountable for quality.\n\n"
            f"Happy to do a small test task first. What's your timeline?"
            f"{note_part}\n\n"
            f"— sent via my LazyClaw setup"
        )

    return (
        f"Hi u/{username},\n\n"
        f"Saw your post{title_clip}. I'm a {profile.title or 'developer'} "
        f"working with {skills_str}. "
        f"{profile.bio[:160] if profile.bio else 'I deliver focused work fast.'}\n\n"
        f"Happy to chat about scope and timing — quick test task first if you want."
        f"{note_part}"
    )
