"""MCP server wrapping instagrapi for Instagram access — DMs, feed, posting."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from mcp_instagram.session_manager import InstagramSessionManager

logger = logging.getLogger(__name__)

app = Server("mcp-instagram")

_sessions: dict[str, InstagramSessionManager] = {}

_DEFAULT_DATA_DIR = str(
    Path(os.path.dirname(__file__), "..", "..", "data", "instagram_sessions").resolve()
)


def _get_session(username: str) -> InstagramSessionManager | None:
    """Look up an active session by username."""
    return _sessions.get(username)


def _text(content: str) -> list[TextContent]:
    """Wrap a string into the MCP TextContent response format."""
    return [TextContent(type="text", text=content)]


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="instagram_setup",
            description="Login to Instagram. Creates a persistent session with anti-ban device fingerprinting.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username"},
                    "password": {"type": "string", "description": "Instagram password"},
                    "totp_seed": {
                        "type": "string",
                        "description": "TOTP seed for 2FA (optional)",
                        "default": "",
                    },
                    "proxy": {
                        "type": "string",
                        "description": "Proxy URL, e.g. http://user:pass@host:port (optional)",
                        "default": "",
                    },
                    "data_dir": {
                        "type": "string",
                        "description": "Directory for session/device files (optional)",
                        "default": "",
                    },
                },
                "required": ["username", "password"],
            },
        ),
        Tool(
            name="instagram_verify",
            description="Submit a 2FA or challenge verification code for a pending Instagram login.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username"},
                    "code": {"type": "string", "description": "Verification code"},
                },
                "required": ["username", "code"],
            },
        ),
        Tool(
            name="instagram_status",
            description="Check Instagram connection status for one or all sessions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {
                        "type": "string",
                        "description": "Instagram username (omit to check all sessions)",
                        "default": "",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="instagram_read_dms",
            description="Read Instagram direct messages.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username (your account)"},
                    "limit": {"type": "integer", "description": "Max threads to fetch", "default": 10},
                    "unread_only": {"type": "boolean", "description": "Only unread threads", "default": True},
                },
                "required": ["username"],
            },
        ),
        Tool(
            name="instagram_send_dm",
            description="Send an Instagram direct message to a user.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username (your account)"},
                    "to_username": {"type": "string", "description": "Recipient Instagram username"},
                    "message": {"type": "string", "description": "Message text to send"},
                },
                "required": ["username", "to_username", "message"],
            },
        ),
        Tool(
            name="instagram_read_feed",
            description="Read your timeline feed or a specific user's posts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username (your account)"},
                    "target_username": {
                        "type": "string",
                        "description": "Target user to read posts from (omit for your timeline)",
                        "default": "",
                    },
                    "limit": {"type": "integer", "description": "Max posts to fetch", "default": 10},
                },
                "required": ["username"],
            },
        ),
        Tool(
            name="instagram_post",
            description="Post a photo to Instagram.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username (your account)"},
                    "image_path": {"type": "string", "description": "Absolute path to the image file"},
                    "caption": {"type": "string", "description": "Post caption", "default": ""},
                },
                "required": ["username", "image_path"],
            },
        ),
        Tool(
            name="instagram_read_profile",
            description="Get profile information for an Instagram user.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username (your account)"},
                    "target_username": {"type": "string", "description": "Username to look up"},
                },
                "required": ["username", "target_username"],
            },
        ),
        Tool(
            name="instagram_search_users",
            description="Search for Instagram users by keyword.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username (your account)"},
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results", "default": 5},
                },
                "required": ["username", "query"],
            },
        ),
        # ── Stories ──────────────────────────────────────────────────
        Tool(
            name="instagram_post_story",
            description="Post a story (photo or video). Disappears after 24h.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username (your account)"},
                    "file_path": {"type": "string", "description": "Absolute path to image or video file"},
                    "caption": {"type": "string", "description": "Story caption/text overlay", "default": ""},
                    "mentions": {
                        "type": "string",
                        "description": "Comma-separated usernames to mention (e.g. 'user1,user2')",
                        "default": "",
                    },
                },
                "required": ["username", "file_path"],
            },
        ),
        Tool(
            name="instagram_read_stories",
            description="View stories from a user or your feed. Returns story metadata and media URLs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username (your account)"},
                    "target_username": {
                        "type": "string",
                        "description": "User whose stories to view (omit for your story feed)",
                        "default": "",
                    },
                },
                "required": ["username"],
            },
        ),
        # ── Reels / Video ────────────────────────────────────────────
        Tool(
            name="instagram_post_reel",
            description="Post a reel (short video). Supports caption and thumbnail.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username (your account)"},
                    "video_path": {"type": "string", "description": "Absolute path to video file"},
                    "caption": {"type": "string", "description": "Reel caption", "default": ""},
                    "thumbnail_path": {
                        "type": "string",
                        "description": "Absolute path to thumbnail image (optional, auto-generated if omitted)",
                        "default": "",
                    },
                },
                "required": ["username", "video_path"],
            },
        ),
        # ── Carousel ─────────────────────────────────────────────────
        Tool(
            name="instagram_post_carousel",
            description="Post a carousel (multiple photos/videos in one post).",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username (your account)"},
                    "file_paths": {
                        "type": "string",
                        "description": "Comma-separated absolute paths to images/videos (2-10 files)",
                    },
                    "caption": {"type": "string", "description": "Post caption", "default": ""},
                },
                "required": ["username", "file_paths"],
            },
        ),
        # ── Comments ─────────────────────────────────────────────────
        Tool(
            name="instagram_read_comments",
            description="Read comments on a post. Use the post URL or media ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username (your account)"},
                    "post_url": {
                        "type": "string",
                        "description": "Post URL (e.g. https://www.instagram.com/p/ABC123/) or media PK/ID",
                    },
                    "limit": {"type": "integer", "description": "Max comments to fetch", "default": 20},
                },
                "required": ["username", "post_url"],
            },
        ),
        Tool(
            name="instagram_comment",
            description="Post a comment on a post, or reply to an existing comment.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username (your account)"},
                    "post_url": {
                        "type": "string",
                        "description": "Post URL or media PK/ID",
                    },
                    "text": {"type": "string", "description": "Comment text"},
                    "reply_to_comment_id": {
                        "type": "string",
                        "description": "Comment ID to reply to (omit for top-level comment)",
                        "default": "",
                    },
                },
                "required": ["username", "post_url", "text"],
            },
        ),
        # ── Likes ────────────────────────────────────────────────────
        Tool(
            name="instagram_like",
            description="Like a post or comment.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username (your account)"},
                    "post_url": {"type": "string", "description": "Post URL or media PK/ID"},
                    "comment_id": {
                        "type": "string",
                        "description": "Comment ID to like (omit to like the post itself)",
                        "default": "",
                    },
                },
                "required": ["username", "post_url"],
            },
        ),
        Tool(
            name="instagram_unlike",
            description="Unlike a post or comment.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username (your account)"},
                    "post_url": {"type": "string", "description": "Post URL or media PK/ID"},
                    "comment_id": {
                        "type": "string",
                        "description": "Comment ID to unlike (omit to unlike the post itself)",
                        "default": "",
                    },
                },
                "required": ["username", "post_url"],
            },
        ),
        # ── Follow / Unfollow ────────────────────────────────────────
        Tool(
            name="instagram_follow",
            description="Follow an Instagram user.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username (your account)"},
                    "target_username": {"type": "string", "description": "User to follow"},
                },
                "required": ["username", "target_username"],
            },
        ),
        Tool(
            name="instagram_unfollow",
            description="Unfollow an Instagram user.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username (your account)"},
                    "target_username": {"type": "string", "description": "User to unfollow"},
                },
                "required": ["username", "target_username"],
            },
        ),
        # ── DM Thread Reply ──────────────────────────────────────────
        Tool(
            name="instagram_reply_dm",
            description="Reply to an existing DM thread by thread ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Instagram username (your account)"},
                    "thread_id": {"type": "string", "description": "Thread ID from instagram_read_dms"},
                    "message": {"type": "string", "description": "Reply message text"},
                },
                "required": ["username", "thread_id", "message"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    handlers = {
        "instagram_setup": _handle_setup,
        "instagram_verify": _handle_verify,
        "instagram_status": _handle_status,
        "instagram_read_dms": _handle_read_dms,
        "instagram_send_dm": _handle_send_dm,
        "instagram_read_feed": _handle_read_feed,
        "instagram_post": _handle_post,
        "instagram_read_profile": _handle_read_profile,
        "instagram_search_users": _handle_search_users,
        "instagram_post_story": _handle_post_story,
        "instagram_read_stories": _handle_read_stories,
        "instagram_post_reel": _handle_post_reel,
        "instagram_post_carousel": _handle_post_carousel,
        "instagram_read_comments": _handle_read_comments,
        "instagram_comment": _handle_comment,
        "instagram_like": _handle_like,
        "instagram_unlike": _handle_unlike,
        "instagram_follow": _handle_follow,
        "instagram_unfollow": _handle_unfollow,
        "instagram_reply_dm": _handle_reply_dm,
    }
    handler = handlers.get(name)
    if handler is None:
        return _text(f"Unknown tool: {name}")
    try:
        return await handler(arguments)
    except Exception as exc:
        logger.error("%s failed: %s", name, exc, exc_info=True)
        return _text(f"Error in {name}: {exc}")


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

async def _handle_setup(args: dict) -> list[TextContent]:
    username = args["username"]
    password = args["password"]
    totp_seed = args.get("totp_seed", "")
    proxy = args.get("proxy", "")
    data_dir = args.get("data_dir", "") or _DEFAULT_DATA_DIR

    session_dir = os.path.join(data_dir, username)

    mgr = InstagramSessionManager(
        session_dir=session_dir,
        username=username,
        password=password,
        proxy=proxy,
        totp_seed=totp_seed,
    )

    success = await asyncio.to_thread(mgr.login)

    if success:
        _sessions[username] = mgr
        return _text(f"Connected as @{username}")

    # Login failed — might be a challenge
    _sessions[username] = mgr
    if mgr.pending_challenge:
        return _text(json.dumps({
            "status": "challenge_required",
            "username": username,
            "challenge": mgr.pending_challenge,
        }))
    return _text(f"Login failed for @{username}. Check credentials.")


async def _handle_verify(args: dict) -> list[TextContent]:
    username = args["username"]
    code = args["code"]

    mgr = _get_session(username)
    if mgr is None:
        return _text(f"No session found for @{username}. Run instagram_setup first.")

    success = await asyncio.to_thread(mgr.resolve_challenge, code)
    if success:
        return _text(f"Verified and connected as @{username}")
    return _text(f"Verification failed for @{username}. Try again or re-run instagram_setup.")


async def _handle_status(args: dict) -> list[TextContent]:
    username = args.get("username", "")

    if username:
        mgr = _get_session(username)
        if mgr is None:
            return _text(json.dumps({"status": "not_found", "username": username}))
        logged_in = await asyncio.to_thread(mgr.is_logged_in)
        status = "connected" if logged_in else "disconnected"
        if mgr.pending_challenge:
            status = "challenge_required"
        result = {"status": status, "username": username}
        if mgr.pending_challenge:
            result["challenge"] = mgr.pending_challenge
        return _text(json.dumps(result))

    # Report all sessions
    results = []
    for uname, mgr in _sessions.items():
        logged_in = await asyncio.to_thread(mgr.is_logged_in)
        status = "connected" if logged_in else "disconnected"
        if mgr.pending_challenge:
            status = "challenge_required"
        entry = {"status": status, "username": uname}
        if mgr.pending_challenge:
            entry["challenge"] = mgr.pending_challenge
        results.append(entry)
    return _text(json.dumps(results))


async def _handle_read_dms(args: dict) -> list[TextContent]:
    username = args["username"]
    limit = int(args.get("limit", 10))
    unread_only = args.get("unread_only", True)

    mgr = _get_session(username)
    if mgr is None:
        return _text(f"No session for @{username}. Run instagram_setup first.")

    threads = await asyncio.to_thread(mgr.get_pending_dms, limit, unread_only)

    messages = []
    for thread in threads:
        thread_id = str(thread.id) if hasattr(thread, "id") else ""
        for msg in getattr(thread, "messages", []):
            messages.append({
                "thread_id": thread_id,
                "from_user": str(getattr(msg, "user_id", "")),
                "message": str(getattr(msg, "text", "")),
                "timestamp": str(getattr(msg, "timestamp", "")),
            })
    return _text(json.dumps(messages))


async def _handle_send_dm(args: dict) -> list[TextContent]:
    username = args["username"]
    to_username = args["to_username"]
    message = args["message"]

    mgr = _get_session(username)
    if mgr is None:
        return _text(f"No session for @{username}. Run instagram_setup first.")

    user_id = await asyncio.to_thread(
        mgr.client.user_id_from_username, to_username
    )
    await asyncio.to_thread(
        mgr.client.direct_send, message, user_ids=[user_id]
    )
    return _text(f"Sent to @{to_username}")


async def _handle_read_feed(args: dict) -> list[TextContent]:
    username = args["username"]
    target_username = args.get("target_username", "")
    limit = int(args.get("limit", 10))

    mgr = _get_session(username)
    if mgr is None:
        return _text(f"No session for @{username}. Run instagram_setup first.")

    if target_username:
        user_id = await asyncio.to_thread(
            mgr.client.user_id_from_username, target_username
        )
        medias = await asyncio.to_thread(
            mgr.client.user_medias, user_id, limit
        )
    else:
        feed = await asyncio.to_thread(mgr.client.get_timeline_feed)
        medias = feed.get("feed_items", [])[:limit] if isinstance(feed, dict) else []

    posts = []
    for media in medias:
        if hasattr(media, "caption_text"):
            # instagrapi Media object
            posts.append({
                "caption": str(getattr(media, "caption_text", "")),
                "likes": getattr(media, "like_count", 0),
                "comments": getattr(media, "comment_count", 0),
                "url": f"https://www.instagram.com/p/{getattr(media, 'code', '')}/",
                "timestamp": str(getattr(media, "taken_at", "")),
                "media_type": str(getattr(media, "media_type", "")),
            })
        elif isinstance(media, dict):
            # Raw timeline feed dict
            m = media.get("media_or_ad", media)
            caption = m.get("caption", {}) or {}
            posts.append({
                "caption": caption.get("text", "") if isinstance(caption, dict) else str(caption),
                "likes": m.get("like_count", 0),
                "comments": m.get("comment_count", 0),
                "url": f"https://www.instagram.com/p/{m.get('code', '')}/",
                "timestamp": str(m.get("taken_at", "")),
                "media_type": str(m.get("media_type", "")),
            })
    return _text(json.dumps(posts))


async def _handle_post(args: dict) -> list[TextContent]:
    username = args["username"]
    image_path = args["image_path"]
    caption = args.get("caption", "")

    mgr = _get_session(username)
    if mgr is None:
        return _text(f"No session for @{username}. Run instagram_setup first.")

    if not os.path.isfile(image_path):
        return _text(f"Image not found: {image_path}")

    media = await asyncio.to_thread(
        mgr.client.photo_upload, image_path, caption
    )
    code = getattr(media, "code", "")
    url = f"https://www.instagram.com/p/{code}/" if code else ""
    return _text(f"Posted successfully. {url}")


async def _handle_read_profile(args: dict) -> list[TextContent]:
    username = args["username"]
    target_username = args["target_username"]

    mgr = _get_session(username)
    if mgr is None:
        return _text(f"No session for @{username}. Run instagram_setup first.")

    # Self-query: use user_info(own_pk) for fresh authoritative data.
    # user_info_by_username returns public-facing cached data that can be stale.
    is_self = target_username.lower().strip("@") == username.lower()
    if is_self:
        own_pk = mgr.client.user_id
        info = await asyncio.to_thread(mgr.client.user_info, own_pk)
    else:
        # Force fresh fetch (use_cache=False)
        info = await asyncio.to_thread(
            mgr.client.user_info_by_username, target_username, False
        )

    profile = {
        "username": str(getattr(info, "username", "")),
        "full_name": str(getattr(info, "full_name", "")),
        "bio": str(getattr(info, "biography", "")),
        "followers": getattr(info, "follower_count", 0),
        "following": getattr(info, "following_count", 0),
        "posts_count": getattr(info, "media_count", 0),
        "is_private": getattr(info, "is_private", False),
        "is_verified": getattr(info, "is_verified", False),
        "external_url": str(getattr(info, "external_url", "") or ""),
        "category": str(getattr(info, "category_name", "") or ""),
        "profile_pic_url": str(getattr(info, "profile_pic_url", "")),
    }
    return _text(json.dumps(profile))


async def _handle_search_users(args: dict) -> list[TextContent]:
    username = args["username"]
    query = args["query"]
    limit = int(args.get("limit", 5))

    mgr = _get_session(username)
    if mgr is None:
        return _text(f"No session for @{username}. Run instagram_setup first.")

    users = await asyncio.to_thread(mgr.client.search_users, query)
    results = []
    for user in users[:limit]:
        results.append({
            "username": str(getattr(user, "username", "")),
            "full_name": str(getattr(user, "full_name", "")),
            "followers": getattr(user, "follower_count", 0),
            "is_verified": getattr(user, "is_verified", False),
        })
    return _text(json.dumps(results))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_media_pk(client, post_url: str) -> int:
    """Extract media PK from a URL or raw PK/ID string."""
    post_url = post_url.strip()
    # If it's a URL, extract the shortcode and resolve
    if "instagram.com" in post_url:
        code = client.media_pk_from_url(post_url)
        return int(code)
    # If it's already a numeric PK
    if post_url.isdigit():
        return int(post_url)
    # Might be a shortcode like "ABC123"
    media_pk = client.media_pk_from_code(post_url)
    return int(media_pk)


def _is_video_file(path: str) -> bool:
    """Check if a file path looks like a video."""
    return path.lower().endswith((".mp4", ".mov", ".avi", ".mkv", ".webm"))


# ---------------------------------------------------------------------------
# Story handlers
# ---------------------------------------------------------------------------

async def _handle_post_story(args: dict) -> list[TextContent]:
    username = args["username"]
    file_path = args["file_path"]
    caption = args.get("caption", "")
    mentions_str = args.get("mentions", "")

    mgr = _get_session(username)
    if mgr is None:
        return _text(f"No session for @{username}. Run instagram_setup first.")

    if not os.path.isfile(file_path):
        return _text(f"File not found: {file_path}")

    # Build mentions list
    mentions = []
    if mentions_str:
        from instagrapi.types import StoryMention
        for mention_name in mentions_str.split(","):
            mention_name = mention_name.strip().lstrip("@")
            if mention_name:
                try:
                    user_id = await asyncio.to_thread(
                        mgr.client.user_id_from_username, mention_name
                    )
                    mentions.append(StoryMention(user=await asyncio.to_thread(
                        mgr.client.user_info, user_id
                    ), x=0.5, y=0.5, width=0.5, height=0.1))
                except Exception as exc:
                    logger.warning("Failed to resolve mention @%s: %s", mention_name, exc)

    if _is_video_file(file_path):
        media = await asyncio.to_thread(
            mgr.client.video_upload_to_story,
            file_path,
            caption,
            mentions=mentions if mentions else [],
        )
    else:
        media = await asyncio.to_thread(
            mgr.client.photo_upload_to_story,
            file_path,
            caption,
            mentions=mentions if mentions else [],
        )

    story_id = getattr(media, "pk", "")
    return _text(f"Story posted. ID: {story_id}")


async def _handle_read_stories(args: dict) -> list[TextContent]:
    username = args["username"]
    target_username = args.get("target_username", "")

    mgr = _get_session(username)
    if mgr is None:
        return _text(f"No session for @{username}. Run instagram_setup first.")

    if target_username:
        user_id = await asyncio.to_thread(
            mgr.client.user_id_from_username, target_username
        )
        stories = await asyncio.to_thread(
            mgr.client.user_stories, user_id
        )
    else:
        # Get reel tray (stories from followed users)
        stories_feed = await asyncio.to_thread(mgr.client.get_timeline_feed)
        # Flatten — return own stories for simplicity
        user_id = await asyncio.to_thread(
            mgr.client.user_id_from_username, username
        )
        stories = await asyncio.to_thread(
            mgr.client.user_stories, user_id
        )

    results = []
    for story in stories:
        results.append({
            "id": str(getattr(story, "pk", "")),
            "media_type": str(getattr(story, "media_type", "")),
            "taken_at": str(getattr(story, "taken_at", "")),
            "video_url": str(getattr(story, "video_url", "") or ""),
            "thumbnail_url": str(getattr(story, "thumbnail_url", "") or ""),
        })
    return _text(json.dumps(results))


# ---------------------------------------------------------------------------
# Reel / Video handler
# ---------------------------------------------------------------------------

async def _handle_post_reel(args: dict) -> list[TextContent]:
    username = args["username"]
    video_path = args["video_path"]
    caption = args.get("caption", "")
    thumbnail_path = args.get("thumbnail_path", "")

    mgr = _get_session(username)
    if mgr is None:
        return _text(f"No session for @{username}. Run instagram_setup first.")

    if not os.path.isfile(video_path):
        return _text(f"Video not found: {video_path}")
    if thumbnail_path and not os.path.isfile(thumbnail_path):
        return _text(f"Thumbnail not found: {thumbnail_path}")

    kwargs = {"path": video_path, "caption": caption}
    if thumbnail_path:
        kwargs["thumbnail"] = thumbnail_path

    media = await asyncio.to_thread(
        mgr.client.clip_upload, **kwargs
    )
    code = getattr(media, "code", "")
    url = f"https://www.instagram.com/reel/{code}/" if code else ""
    return _text(f"Reel posted. {url}")


# ---------------------------------------------------------------------------
# Carousel handler
# ---------------------------------------------------------------------------

async def _handle_post_carousel(args: dict) -> list[TextContent]:
    username = args["username"]
    file_paths_str = args["file_paths"]
    caption = args.get("caption", "")

    mgr = _get_session(username)
    if mgr is None:
        return _text(f"No session for @{username}. Run instagram_setup first.")

    paths = [p.strip() for p in file_paths_str.split(",") if p.strip()]
    if len(paths) < 2:
        return _text("Carousel requires at least 2 files.")
    if len(paths) > 10:
        return _text("Carousel supports max 10 files.")

    for p in paths:
        if not os.path.isfile(p):
            return _text(f"File not found: {p}")

    media = await asyncio.to_thread(
        mgr.client.album_upload, paths, caption
    )
    code = getattr(media, "code", "")
    url = f"https://www.instagram.com/p/{code}/" if code else ""
    return _text(f"Carousel posted ({len(paths)} items). {url}")


# ---------------------------------------------------------------------------
# Comment handlers
# ---------------------------------------------------------------------------

async def _handle_read_comments(args: dict) -> list[TextContent]:
    username = args["username"]
    post_url = args["post_url"]
    limit = int(args.get("limit", 20))

    mgr = _get_session(username)
    if mgr is None:
        return _text(f"No session for @{username}. Run instagram_setup first.")

    media_pk = await asyncio.to_thread(_resolve_media_pk, mgr.client, post_url)
    comments = await asyncio.to_thread(
        mgr.client.media_comments, media_pk, limit
    )

    results = []
    for c in comments:
        results.append({
            "id": str(getattr(c, "pk", "")),
            "from_user": str(getattr(c, "user", {}).username if hasattr(getattr(c, "user", None), "username") else ""),
            "text": str(getattr(c, "text", "")),
            "timestamp": str(getattr(c, "created_at_utc", "")),
            "likes": getattr(c, "comment_like_count", 0),
            "reply_count": getattr(c, "child_comment_count", 0),
        })
    return _text(json.dumps(results))


async def _handle_comment(args: dict) -> list[TextContent]:
    username = args["username"]
    post_url = args["post_url"]
    text = args["text"]
    reply_to = args.get("reply_to_comment_id", "")

    mgr = _get_session(username)
    if mgr is None:
        return _text(f"No session for @{username}. Run instagram_setup first.")

    media_pk = await asyncio.to_thread(_resolve_media_pk, mgr.client, post_url)

    if reply_to:
        comment = await asyncio.to_thread(
            mgr.client.media_comment, media_pk, text,
            replied_to_comment_id=int(reply_to),
        )
    else:
        comment = await asyncio.to_thread(
            mgr.client.media_comment, media_pk, text,
        )

    comment_id = str(getattr(comment, "pk", ""))
    return _text(f"Comment posted. ID: {comment_id}")


# ---------------------------------------------------------------------------
# Like / Unlike handlers
# ---------------------------------------------------------------------------

async def _handle_like(args: dict) -> list[TextContent]:
    username = args["username"]
    post_url = args["post_url"]
    comment_id = args.get("comment_id", "")

    mgr = _get_session(username)
    if mgr is None:
        return _text(f"No session for @{username}. Run instagram_setup first.")

    if comment_id:
        await asyncio.to_thread(
            mgr.client.comment_like, int(comment_id)
        )
        return _text(f"Liked comment {comment_id}.")
    else:
        media_pk = await asyncio.to_thread(_resolve_media_pk, mgr.client, post_url)
        await asyncio.to_thread(mgr.client.media_like, media_pk)
        return _text("Liked post.")


async def _handle_unlike(args: dict) -> list[TextContent]:
    username = args["username"]
    post_url = args["post_url"]
    comment_id = args.get("comment_id", "")

    mgr = _get_session(username)
    if mgr is None:
        return _text(f"No session for @{username}. Run instagram_setup first.")

    if comment_id:
        await asyncio.to_thread(
            mgr.client.comment_unlike, int(comment_id)
        )
        return _text(f"Unliked comment {comment_id}.")
    else:
        media_pk = await asyncio.to_thread(_resolve_media_pk, mgr.client, post_url)
        await asyncio.to_thread(mgr.client.media_unlike, media_pk)
        return _text("Unliked post.")


# ---------------------------------------------------------------------------
# Follow / Unfollow handlers
# ---------------------------------------------------------------------------

async def _handle_follow(args: dict) -> list[TextContent]:
    username = args["username"]
    target_username = args["target_username"]

    mgr = _get_session(username)
    if mgr is None:
        return _text(f"No session for @{username}. Run instagram_setup first.")

    user_id = await asyncio.to_thread(
        mgr.client.user_id_from_username, target_username
    )
    await asyncio.to_thread(mgr.client.user_follow, user_id)
    return _text(f"Followed @{target_username}.")


async def _handle_unfollow(args: dict) -> list[TextContent]:
    username = args["username"]
    target_username = args["target_username"]

    mgr = _get_session(username)
    if mgr is None:
        return _text(f"No session for @{username}. Run instagram_setup first.")

    user_id = await asyncio.to_thread(
        mgr.client.user_id_from_username, target_username
    )
    await asyncio.to_thread(mgr.client.user_unfollow, user_id)
    return _text(f"Unfollowed @{target_username}.")


# ---------------------------------------------------------------------------
# DM Thread Reply handler
# ---------------------------------------------------------------------------

async def _handle_reply_dm(args: dict) -> list[TextContent]:
    username = args["username"]
    thread_id = args["thread_id"]
    message = args["message"]

    mgr = _get_session(username)
    if mgr is None:
        return _text(f"No session for @{username}. Run instagram_setup first.")

    await asyncio.to_thread(
        mgr.client.direct_send, message, thread_ids=[int(thread_id)]
    )
    return _text(f"Replied in thread {thread_id}.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the MCP server on stdio."""

    async def _run() -> None:
        async with stdio_server() as (read, write):
            await app.run(read, write, app.create_initialization_options())

    asyncio.run(_run())
