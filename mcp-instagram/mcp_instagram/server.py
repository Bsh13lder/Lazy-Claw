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

    info = await asyncio.to_thread(
        mgr.client.user_info_by_username, target_username
    )
    profile = {
        "username": str(getattr(info, "username", "")),
        "full_name": str(getattr(info, "full_name", "")),
        "bio": str(getattr(info, "biography", "")),
        "followers": getattr(info, "follower_count", 0),
        "following": getattr(info, "following_count", 0),
        "posts_count": getattr(info, "media_count", 0),
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
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the MCP server on stdio."""

    async def _run() -> None:
        async with stdio_server() as (read, write):
            await app.run(read, write, app.create_initialization_options())

    asyncio.run(_run())
