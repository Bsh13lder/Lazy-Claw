# mcp-instagram

**Instagram MCP Server** -- Full Instagram access via the private mobile API (instagrapi). No browser, no official API approval, no Meta developer account needed.

## What It Does

Connects to Instagram's private mobile API, giving your AI agent access to nearly everything the Instagram app can do:

- **DMs**: Read, send, and reply to direct messages
- **Feed**: Read your home feed, any user's profile feed
- **Posting**: Create posts, stories, reels, and carousels
- **Comments**: Read and write comments on any post
- **Interactions**: Like, unlike, follow, unfollow
- **Stories**: View any user's stories
- **Search**: Find users by username or name
- **2FA/TOTP**: Full support for two-factor authentication

## Architecture

```
AI Agent <--stdio--> mcp-instagram <--HTTPS--> Instagram Mobile API
                          |
                    instagrapi library
                          |
                    Session persistence
                    Device fingerprint
```

- **Runtime**: Python 3.11+
- **Transport**: stdio (MCP standard)
- **API**: instagrapi (private Instagram mobile API client)
- **Auth**: Username/password + TOTP 2FA support
- **Anti-ban**: Device fingerprinting, session persistence, realistic delays

## Setup

### Prerequisites
- Python 3.11+
- An Instagram account

### Install
```bash
cd production/mcps/mcp-instagram
pip install -e .
```

### First Run
The AI agent will call `instagram_setup` with your credentials. If you have 2FA enabled, it will prompt for a TOTP code (or you can provide your TOTP secret for automatic generation).

### Optional: Proxy Support
To reduce ban risk, use a residential proxy:
```bash
export INSTAGRAM_PROXY=http://user:pass@proxy.example.com:8080
```

### Register with LazyClaw
```json
{
  "name": "mcp-instagram",
  "command": "python",
  "args": ["-m", "mcp_instagram"],
  "transport": "stdio"
}
```

## Available Tools (20)

| Tool | Description |
|------|-------------|
| `instagram_setup` | Login with username/password (+ optional TOTP secret) |
| `instagram_verify` | Submit 2FA verification code |
| `instagram_status` | Check login status |
| `instagram_read_dms` | Read recent DMs (unread or all) |
| `instagram_send_dm` | Send DM to a user |
| `instagram_reply_dm` | Reply to a specific DM thread |
| `instagram_read_feed` | Read home feed posts |
| `instagram_read_profile` | View a user's profile and recent posts |
| `instagram_search_users` | Search for users |
| `instagram_post` | Create a new photo/video post |
| `instagram_post_story` | Post a story |
| `instagram_post_reel` | Post a reel |
| `instagram_post_carousel` | Post a multi-image carousel |
| `instagram_read_comments` | Read comments on a post |
| `instagram_comment` | Comment on a post |
| `instagram_like` | Like a post |
| `instagram_unlike` | Unlike a post |
| `instagram_follow` | Follow a user |
| `instagram_unfollow` | Unfollow a user |
| `instagram_read_stories` | View a user's stories |

## Anti-Ban Measures

The session manager implements several anti-detection strategies:
- **Device fingerprinting** -- Generates and persists a realistic Android device profile
- **Session persistence** -- Reuses Instagram session tokens across restarts
- **Realistic timing** -- Built-in delays between operations (handled by instagrapi)
- **Challenge resolution** -- Handles Instagram security challenges programmatically

## Data Storage

```
data/instagram_sessions/
  {username}/
    device.json       # Device fingerprint
    session.json      # Login session token
```

---

## Analysis

### Viral Potential: HIGH

**Reasoning**: Instagram has 2+ billion users. An AI that can manage your DMs, post content, and interact with followers is extremely compelling for creators, businesses, and power users. "My AI manages my Instagram" is headline-worthy. The 20-tool coverage makes this feel like a complete product, not a demo.

**Key viral moments**:
- AI reads and responds to DMs while you sleep
- AI posts scheduled content (stories, reels, carousels)
- AI monitors comments and engages with followers
- AI searches for and follows relevant accounts
- Content creators automating their entire Instagram workflow

### Known Bugs & Issues

1. **Private API risk** -- instagrapi uses Instagram's undocumented private API. Instagram actively fights automation. Accounts can be temporarily locked or permanently banned. This must be disclosed prominently.
2. **No rate limiting** -- The server doesn't implement its own rate limiting. Rapid-fire tool calls (e.g., liking 100 posts) will trigger Instagram's abuse detection immediately.
3. **Session expiry not handled gracefully** -- If the Instagram session expires mid-operation, some tools will fail with cryptic errors instead of prompting re-login.
4. **TOTP secret stored in memory only** -- If the server restarts, the user must re-enter their 2FA code. The TOTP secret isn't persisted.
5. **No media download** -- Can view stories and posts but can't save/download media files.
6. **Challenge resolver is basic** -- Instagram's security challenges are increasingly complex. The current resolver handles simple challenges but may fail on newer ones.
7. **Credentials in plaintext** -- Login credentials aren't encrypted at rest.

### Public vs Private Recommendation: PUBLIC

**Recommendation**: Open-source. This is one of LazyClaw's strongest differentiators.

**Why public**:
- Highest tool count (20) of any MCP -- feels like a complete product, not a demo
- instagrapi is already public on PyPI with 10K+ GitHub stars -- Meta hasn't taken it down
- Creator economy is massive -- influencers, brands, and agencies will find this immediately useful
- "My AI manages my Instagram" is headline-worthy, shareable content
- No other open-source AI agent platform has Instagram integration this deep

**Disclaimers to include**:
- Uses Instagram's private mobile API -- not officially supported by Meta
- Aggressive automation may result in temporary locks or bans
- Users are responsible for complying with Instagram's Terms of Service
- Recommend using a secondary account for testing before connecting a primary account
