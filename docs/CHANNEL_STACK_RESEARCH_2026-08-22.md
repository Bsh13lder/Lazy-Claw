# Channel Stack Research — WhatsApp / Email / Instagram

**Date:** 2026-08-22
**Scope:** (a) fix what is broken in LazyClaw's `mcp-whatsapp`, `mcp-email`, `mcp-instagram`; (b) pick the right stack for **Hirossa**, a customer-service chatbot product intended for public launch.

---

## 0. TL;DR

| Concern | Verdict |
|---|---|
| **Email license** | You were right — but it's **IMAP, not SMTP**. `aioimaplib` is **GPL-3.0**. `aiosmtplib` is MIT and fine. **RESOLVED 2026-08-22:** it was declared but never imported — the server has always used stdlib `imaplib` (PSF) via `asyncio.to_thread`. Deleting one line removed GPL-3.0 from the shipped image. No `imap-tools` migration needed. |
| **WhatsApp missing names** | **Root-caused, not a mystery.** Saved address-book names live in WhatsApp's `critical_unblock_low` app-state collection. `index.js:634` resyncs `regular_high`, `regular_low`, `critical_block` — it never asks for `critical_unblock_low`. So `Contact.name` is always empty and you fall through to `notify` (the name *they* chose) or the raw JID. |
| **WhatsApp missing numbers** | LID migration. `extractPhone()` returns `null` for anything not `@s.whatsapp.net` (`index.js:1013`), and every `@lid` contact hits that. Baileys 7 hands you `Contact.lid` + `Contact.phoneNumber` directly; your code reads `c.jid`, a field **that does not exist** on the type (`index.js:699`), so it silently falls back to `c.id`. |
| **WhatsApp fragility** | You're on Baileys `^6.7.16`; latest is **7.0.0-rc14**, which added a real `LIDMappingStore`. Your hand-rolled "two contacts share a name ⇒ same person" heuristic (`_buildLidMap`, `index.js:295`) is a guess that 7.x makes unnecessary. |
| **Hirossa** | **Do not ship Baileys or instagrapi in a product you sell.** Both are reverse-engineered private APIs; the customer's number/account gets banned and *you* get the support ticket. Hirossa must run on **WhatsApp Cloud API** + **Instagram Messaging API** (both official, both free to access, both plain REST → zero new license risk). |
| **Architecture** | Split the transport from the logic. One `ChannelAdapter` interface, two implementations per channel: `unofficial` (LazyClaw personal) and `official` (Hirossa product). Same agent brain on top. |

---

## 1. What is actually wrong today — with evidence

### 1.1 `mcp-email` — GPL-3.0 contamination (CONFIRMED)

`mcp-email/pyproject.toml` declares `license = {text = "MIT"}` and then depends on:

```
aiosmtplib>=3.0.0    → MIT              ✅
aioimaplib>=1.1.0    → GPL-3.0          ❌
```

Verified against the installed distribution:

```
=== aioimaplib ===
License: GPL-3.0
Classifier: License :: OSI Approved :: GNU General Public License v3 (GPLv3)
Version: 2.0.1
```

This is the same class of problem as PyMuPDF/HyperFormula in `CLAUDE.md`'s ban list. It is arguably *worse*, because GPL-3.0 (unlike AGPL) still triggers on distribution — and you distribute an installable package and a Docker image.

**Replacements, all verified:**

| Library | License | Async? | Stars | Last push | Notes |
|---|---|---|---|---|---|
| **`imap-tools`** | **Apache-2.0** | No (sync) | 840 | 2026-08-06 | High-level, typed, IDLE support, actively maintained. Run in `asyncio.to_thread`. **Recommended.** |
| `IMAPClient` | BSD-3-Clause | No (sync) | 561 | 2026-06-28 | Lower-level, very stable, 66 open issues. |
| stdlib `imaplib` | PSF (permissive) | No | — | — | Zero deps, but you hand-roll parsing. |
| `aioimaplib` | **GPL-3.0** | Yes | — | — | **Remove.** |

The async-ness loss is not real: IMAP is I/O-bound and `asyncio.to_thread` around a sync client is the standard pattern. `imap-tools` also gives you `MailBox.fetch()` with parsed envelopes, so you delete a chunk of `server.py` in the process.

For MIME parsing, if you need more than stdlib `email`: `mail-parser` is Apache-2.0.

### 1.2 `mcp-whatsapp` — the two name/number bugs

**Bug A — saved contact names are never requested.**

Baileys' `Contact` type documents exactly what you want:

```ts
/** name of the contact, you have saved on your WA */
name?: string
/** name of the contact, the contact has set on their own on WA */
notify?: string
```

`Contact.name` is populated from the `contactAction` sync-action. In Baileys' `src/Utils/chat-utils.ts`, that action is bound to a specific app-state collection:

```ts
} else if ('contact' in mod) {
    patch = {
        syncAction: { contactAction: mod.contact || {} },
        index: ['contact', jid],
        type: 'critical_unblock_low',   // ← here
        ...
```

Your code (`mcp-whatsapp/src/index.js:634`):

```js
await sock.resyncAppState(["regular_high", "regular_low", "critical_block"], false);
```

`critical_unblock_low` is missing. And the incremental resync at `index.js:937` asks only for `regular_high`. Net effect: `c.name` is empty forever, `_displayNameForJid()` (`index.js:1031`) falls through to `c.notify`, and you see whatever the *other person* typed as their own display name — or nothing.

**Fix:** add `"critical_unblock_low"` to both resync calls. One-line change, highest value-per-byte fix in this document.

**Bug B — phone numbers vanish under LID.**

```js
function extractPhone(jid) {
  if (!jid || !jid.endsWith("@s.whatsapp.net")) return null;   // index.js:1013
  ...
}
```

Every `@lid` JID returns `null`. Since WhatsApp's LID migration, a large and growing share of your chats are addressed by `@lid`, so the number column goes blank. That is precisely the symptom you described.

Compounding it, at `index.js:699`:

```js
const cJid = c.jid || c.id || "";
```

`Contact` has **no `jid` field**. It has `id`, `lid`, and `phoneNumber`. So `cJid` is always `c.id`, and when `c.id` is a LID, `extractPhone(cJid)` → `null`. WhatsApp is literally handing you the phone number in `c.phoneNumber` and the code never reads it.

**Bug C — the LID map is a name-collision guess.**

`_buildLidMap()` (`index.js:295`) groups contacts by lowercased name and links a pair when a name maps to exactly one `@s.whatsapp.net` and one `@lid`. That is a heuristic with two failure modes: two contacts with the same name never link, and an empty name (Bug A guarantees many) links nothing at all. Baileys 7 ships `src/Signal/lid-mapping.ts` with a real bidirectional `LIDMappingStore` (`getPNForLID` / `getLIDForPN`, LRU + 3-day TTL, in-flight dedup) plus a `lid-mapping.update` event, sourced from WhatsApp's own mapping metadata.

**Caveat worth knowing before you budget the work:** even Baileys 7 cannot always resolve LID→PN. Per the maintainers' guidance, WhatsApp omits mapping metadata from some history-sync payloads entirely, and *"WhatsApp lets you resolve a phone number to its LID, but the reverse is not generally supported."* Storing the LID as a stable fallback identifier is the intended design, not a workaround. So: expect a large improvement, not 100%.

**The move you already have and aren't using:** LazyClaw has an encrypted unified contact store with macOS Contacts sync. `fetchUnifiedContacts()` (`index.js:76`) already calls it — but only for *outbound* resolution in `whatsapp_send`. Wire it into `_displayNameForJid()` as a tier-0 lookup and you get your own address-book names on inbound messages regardless of what WhatsApp does with LID. That is the most robust fix available, because it depends on data you control.

### 1.3 `mcp-instagram` — licensed fine, but wrong for a product

```
instagrapi>=2.0.0   → MIT (LICENSE file confirms; GitHub shows NOASSERTION only
                      because of an added attribution paragraph for instagram_mqtt)
pyotp>=2.9.0        → MIT
```

No license problem. The problem is category: `instagrapi` drives Instagram's **private mobile API**. It is fine for your own account in LazyClaw. It is a liability in a product — see §3.

---

## 2. The landscape, with real numbers

All figures pulled 2026-08-22 from the GitHub and npm APIs.

### WhatsApp transports

| Project | License | Stars | Last push | Approach | Verdict |
|---|---|---|---|---|---|
| **WhiskeySockets/Baileys** | MIT | 10,803 | 2026-08-16 | Native WebSocket, TS | **Stay.** Upgrade 6.7 → 7.x. 337 open issues reflects volume, not rot. |
| **tulir/whatsmeow** | MPL-2.0 | 7,103 | 2026-08-21 | Native WebSocket, Go | Most *stable* option (58 open issues). MPL-2.0 is file-level copyleft — compatible with MIT as a **separate process**, which is exactly how you run MCP servers. |
| wwebjs/whatsapp-web.js | Apache-2.0 | 22,450 | 2026-08-20 | Puppeteer + Chromium | Most stars, worst runtime profile: ~500 MB RSS vs tens of MB. You already went CDP-only elsewhere; don't regress. |
| devlikeapro/waha | Apache-2.0 | 7,252 | 2026-08-21 | REST wrapper over all 4 engines | Interesting escape hatch: swap engine via one env var when WhatsApp breaks one. But 463 open issues, and Plus is a paid Docker image. |
| @wppconnect-team/wppconnect | **LGPL-3.0-or-later** | — | 2026-08-18 | Puppeteer | **Reject** — copyleft, violates your permissive-only rule. |

**Existing WhatsApp MCP servers — do not adopt any of them:**

| Project | License | Stars | Last push |
|---|---|---|---|
| lharries/whatsapp-mcp | MIT | 6,185 | **2025-07-13** (13 months stale, 233 open issues) |
| FelixIsaac/whatsapp-mcp-extended | MIT | 23 | 2026-08-11 |
| AuraFriday/whatsapp_mcp | MPL-2.0 | 6 | 2025-11-14 |
| Sealjay/mcp-whatsapp | MIT | 8 | 2026-07-18 |

The popular one is abandoned; the maintained ones are tiny. Your 2,166-line `index.js` is already more capable than all of them. **Fix yours, don't fork theirs.**

### Instagram

| Path | License / access | Verdict |
|---|---|---|
| `instagrapi` (private API) | MIT, 6,690★, active | Personal use only. |
| **Instagram Messaging API** (official) | Meta Graph API, REST | The product answer. Confirmed from Meta's own docs: the *Instagram API with Instagram Login* setup **does not require a linked Facebook Page**. Needs a Professional (Business/Creator) account and the `instagram_business_manage_messages` scope. |

Existing Instagram MCP servers are all small and all wrap the Graph API: `jlbadano/ig-mcp` (MIT, 178★, last push 2026-02-09), `mcpware/instagram-mcp` (MIT, 30★), `mikusnuz/meta-mcp` (MIT, 19★). Worth reading `jlbadano/ig-mcp` for its tool shapes; not worth depending on.

### Email

Covered in §1.1. Existing email MCP servers for reference only: `Wh1isper/mcp-email-server` (BSD-3, 315★, active), `n24q02m/better-email-mcp` (Apache-2.0, 31★). Yours is fine once the GPL dep is gone.

---

## 3. The fork in the road: LazyClaw ≠ Hirossa

This is the most important conclusion in the document, so it gets its own section.

**LazyClaw is your personal agent.** One account, your account, your risk. Baileys and instagrapi are correct choices: full inbox access, no approval process, no per-message cost, works with a normal personal number.

**Hirossa is a product you want to sell.** Every unofficial-library decision you make becomes a support burden multiplied by your customer count:

- Both `whatsmeow` and Baileys are, per WhatsApp's terms, unauthorized third-party clients. Community reports put ban timelines in the **2–8 week range** once a number trips detection — treat that figure as directional (it comes from vendor blogs, not Meta), but the direction is not in dispute.
- A banned number is not recoverable by you. Your customer loses their business line and blames Hirossa.
- WhatsApp ships protocol changes on its own schedule; when it does, every unofficial client stops until a maintainer patches. You'd be selling an SLA you cannot honour.
- Instagram's private API is worse: account bans there are frequent and the account *is* the business.

### The official path is genuinely good now

**WhatsApp Cloud API**
- No subscription fee. You pay per message.
- Since 1 July 2025, pricing is **per-message** for templates, priced by recipient country and category (marketing / utility / authentication / service).
- Service messages inside the 24-hour customer-service window are currently **free** — but **from 1 October 2026** service replies and utility messages inside that window start being charged at utility/authentication rates. That's ~6 weeks out. Model it into Hirossa's pricing now, not later.
- Auth messages run roughly $0.0014 (India) to $0.05+ (some EU markets) — confirm current rates for your target countries against Meta's own pricing page before you publish a price.

**Instagram Messaging API**
- Business Login with `instagram_business_basic` + `instagram_business_manage_messages`.
- No Facebook Page required (verified against Meta docs).
- Send access requires **App Review / Advanced Access**, typically **5–10 business days**. Start this immediately — it is the long pole in any launch timeline.
- Rate limits to design around: **200 automated messages/hour/account**, one automated message per user per trigger, promotional content only within 24h of the user's last message.

**Email** — plain IMAP/SMTP is already official. No change needed beyond §1.1.

### Both APIs are plain REST

This matters for your license discipline: WhatsApp Cloud API and Instagram Messaging API are HTTPS + JSON. `httpx` (BSD-3) is the entire dependency. No reverse-engineered client, no GPL, nothing to audit. Meanwhile every open-source Cloud-API MCP server I found is ≤3 stars or unlicensed (`NicolaiSchmid/pons` ★1 no license, `networkerman/...` ★0 no license, `nakulben/whatsapp-mcp` MIT ★3). **Write your own — it's ~300 lines and you'll understand every one of them.**

---

## 4. Recommended architecture

### 4.1 One interface, two backends

The mistake to avoid is building Hirossa's channels as a second copy of LazyClaw's. Define the contract once:

```
ChannelAdapter
  ├─ send_text(conversation_id, text) -> message_id
  ├─ send_media(conversation_id, media) -> message_id
  ├─ fetch_history(conversation_id, limit) -> [Message]
  ├─ resolve_identity(handle) -> Identity{display_name, phone?, avatar?}
  └─ on_inbound(callback)                     # webhook or socket event

Message   { id, conversation_id, direction, author: Identity, body, media[], ts, reply_to }
Identity  { channel, stable_id, display_name, phone?, verified? }
```

`Identity.stable_id` is the key design decision. Make it **opaque** — for WhatsApp it may be a LID that never resolves to a number, and that has to be a first-class supported state, not an error path. `phone` is `Optional`. Bake that in now and the LID problem stops being a bug and becomes a documented property.

Implementations:

| Channel | LazyClaw adapter | Hirossa adapter |
|---|---|---|
| WhatsApp | Baileys 7.x (existing `mcp-whatsapp`) | Cloud API (new, ~300 LOC REST) |
| Instagram | instagrapi (existing) | Messaging API (new, ~250 LOC REST) |
| Email | `aiosmtplib` + `imap-tools` | same — shared |

Same agent brain, same skill registry, same encrypted storage on top. Two transports.

### 4.2 Inbound is webhooks, not polling

Cloud API and Instagram Messaging both push webhooks. Design Hirossa around a webhook receiver from day one:

- Verify Meta's `X-Hub-Signature-256` on every request — this is mandatory, not optional hardening.
- **Ack in <5s, process async.** Meta retries on timeout; slow handlers cause duplicate deliveries.
- **Idempotency by provider message ID.** Retries *will* happen. Dedup at ingest.
- Queue inbound into your existing lane queue so per-conversation FIFO is preserved (you already have this in `queue/`).

### 4.3 The 24-hour window is a state machine, not a detail

For customer service this is the single most consequential business rule, and it's about to get more expensive (1 Oct 2026):

```
conversation.window_expires_at = last_inbound_user_message_ts + 24h
  inside window  → free-form reply, cheap (free until Oct 2026, then utility-rate)
  outside window → approved template only, always billed
```

Hirossa should track this per conversation, surface it in the agent's context ("you have 3h 12m of free-form window left"), and *refuse* to generate a free-form reply outside it rather than failing at the API. Template management (create / submit for approval / track status) needs to be a first-class feature, not an afterthought.

### 4.4 Don't rebuild the agent-inbox wheel — but check the license

**Chatwoot** (36,062★, active daily) is the reference open-source omnichannel customer-service platform. License nuance that matters to you: **MIT except everything under `enterprise/`**, which is under a separate commercial license. So it's usable, but you must be careful never to copy from `enterprise/`.

Realistic options, in order of how much you build yourself:

1. **Hirossa = agent layer, Chatwoot = inbox.** Chatwoot already solves human handoff, agent seats, canned responses, SLA reporting, and it has native WhatsApp Cloud API + Instagram channels. You plug in as an AI agent via its API. Fastest path to a launchable product; you compete on agent quality, not on rebuilding a helpdesk.
2. **Hirossa standalone, borrow the data model.** Read Chatwoot's conversation/contact/inbox schema, implement your own. More work, full control, no license entanglement.
3. Full green field. Only if Hirossa's differentiator *is* the inbox.

Also worth knowing: **Botpress** (MIT, 14,874★) and **Rasa** (Apache-2.0, 21,301★) are the permissively-licensed chatbot frameworks. Both are heavier than what you need if the LLM is doing the reasoning — you have a brain already. **Papercups** (MIT, 6,100★) is abandoned since Feb 2024; ignore it.

---

## 5. Concrete upgrade plan

Ordered by value-per-effort. Sizes are rough.

### Now — LazyClaw, unblocks your daily use

| # | Change | Effort | Why |
|---|---|---|---|
| 1 | Add `"critical_unblock_low"` to both `resyncAppState` calls (`index.js:634`, `:937`) | **1 line** | Saved contact names start arriving. Direct fix for your complaint. |
| 2 | Read `c.lid` / `c.phoneNumber` instead of the nonexistent `c.jid` (`index.js:699`) | ~10 lines | Restores phone numbers wherever WhatsApp provides them. |
| 3 | Make `fetchUnifiedContacts()` tier-0 in `_displayNameForJid()` (`index.js:1031`) | ~30 lines | Your own address book beats anything WhatsApp does or doesn't sync. Survives LID entirely. |
| 4 | ~~Replace `aioimaplib` with `imap-tools`~~ — **DONE 2026-08-22**, and it was a one-line delete: the dep was declared but never imported. Gate extended to scan declared deps in every `pyproject.toml`, not just installed ones. | done | GPL-3.0 no longer ships in the Docker image. |

### Next — LazyClaw, removes the fragility

| # | Change | Effort |
|---|---|---|
| 5 | Upgrade Baileys `^6.7.16` → `7.x`; adopt `signalRepository.lidMapping` and the `lid-mapping.update` event; delete `_buildLidMap()` | 2–3 days (7.x is still RC — pin exactly, per your existing `==` pin convention for vendored deps) |
| 6 | Persist the LID↔PN map to disk alongside `contacts.json` so it survives restarts | ~half day |
| 7 | Investigate the duplicate `messaging-history.set` handlers (`index.js:683` and `:879`) | ~1h — two handlers on one event is either intentional separation or a merge artifact; worth confirming |
| 8 | Split `index.js` (2,166 lines) into `contacts.js` / `chats.js` / `send.js` / `tools.js` | ~1 day — your own style rules cap files at 800 |

### Hirossa — product track, start in parallel

| # | Change | Effort | Note |
|---|---|---|---|
| 9 | **Submit Instagram App Review today** | ~2h + 5–10 business days wait | Longest lead time of anything here. Blocking. |
| 10 | Meta Business verification + WhatsApp Cloud API number registration | ~1 day + verification wait | Also has a queue. |
| 11 | Define the `ChannelAdapter` interface (§4.1) and retrofit the existing MCP servers behind it | 2–3 days | Do this *before* writing adapter #2, or you'll write it twice. |
| 12 | WhatsApp Cloud API adapter — send, webhook receive, media, template CRUD | ~3 days | Plain `httpx`. |
| 13 | Instagram Messaging API adapter | ~2 days | Same shape. |
| 14 | Webhook receiver: signature verification, <5s ack, idempotency by provider message ID, lane-queue handoff | ~2 days | §4.2 — get this right once. |
| 15 | 24-hour window state machine + template fallback | ~2 days | §4.3. Price in the 1 Oct 2026 change. |
| 16 | Decide: Chatwoot-as-inbox vs standalone (§4.4) | Decision, then 1 week or 4+ | Recommend option 1 for launch. |

**Do not** port Baileys or instagrapi into Hirossa. If you need an unofficial fallback for customers who refuse Cloud API onboarding, ship it as a clearly-labelled, separately-priced "unsupported / at your own risk" tier — behind the same `ChannelAdapter`, so it costs you nothing architecturally.

---

## 6. Open questions for you

1. **Hirossa multi-tenancy** — one Meta app with per-customer OAuth tokens (you handle App Review once, customers just log in), or does each customer bring their own Meta app? The first is much better UX and much more work on token storage/rotation. Assumed the first above.
2. **Human handoff** — does Hirossa need a live-agent inbox at launch? If yes, §4.4 option 1 (Chatwoot) becomes near-mandatory for the timeline.
3. **Target countries** — Cloud API per-message pricing varies by an order of magnitude across markets. Needed before you can price Hirossa.

---

## Sources

- [Baileys — GitHub](https://github.com/WhiskeySockets/Baileys) · [`src/Types/Contact.ts`](https://github.com/WhiskeySockets/Baileys/blob/master/src/Types/Contact.ts) · [`src/Utils/chat-utils.ts`](https://github.com/WhiskeySockets/Baileys/blob/master/src/Utils/chat-utils.ts) · [`src/Signal/lid-mapping.ts`](https://github.com/WhiskeySockets/Baileys/blob/master/src/Signal/lid-mapping.ts)
- [Baileys Discussion #2551 — resolving @lid to phone JID](https://github.com/WhiskeySockets/Baileys/discussions/2551)
- [Baileys Issue #2414 — LID mapping best practices](https://github.com/WhiskeySockets/Baileys/issues/2414)
- [Baileys — JIDs concept docs](https://baileys.wiki/concepts/jids)
- [tulir/whatsmeow](https://github.com/tulir/whatsmeow) · [wwebjs/whatsapp-web.js](https://github.com/wwebjs/whatsapp-web.js) · [devlikeapro/waha](https://github.com/devlikeapro/waha) · [WAHA engines docs](https://waha.devlike.pro/docs/how-to/engines/)
- [lharries/whatsapp-mcp](https://github.com/lharries/whatsapp-mcp) · [FelixIsaac/whatsapp-mcp-extended](https://github.com/FelixIsaac/whatsapp-mcp-extended) · [Sealjay/mcp-whatsapp](https://github.com/Sealjay/mcp-whatsapp)
- [2Chat — comparison of open-source WhatsApp libraries](https://2chat.co/alternatives/open-source-whatsapp-libraries) (ban-timeline figures; vendor blog, treat as directional)
- [ikvk/imap_tools](https://github.com/ikvk/imap_tools) · [mjs/imapclient](https://github.com/mjs/imapclient) · [bamthomas/aioimaplib](https://github.com/bamthomas/aioimaplib)
- [subzeroid/instagrapi](https://github.com/subzeroid/instagrapi)
- [Meta — Instagram API with Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login)
- [Meta — WhatsApp Business Platform pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)
- [Instagram Messaging API approval guide (2026)](https://singhamandeep.com/instagram-messaging-api-approval-getting-instagram_business_manage_messages-2026/)
- [WhatsApp API pricing 2026 — free 24h window ends October](https://blog.peppercloud.com/whatsapp-api-pricing-everything-you-need-to-know/)
- [chatwoot/chatwoot](https://github.com/chatwoot/chatwoot) · [botpress/botpress](https://github.com/botpress/botpress) · [RasaHQ/rasa](https://github.com/RasaHQ/rasa)
- [jlbadano/ig-mcp](https://github.com/jlbadano/ig-mcp) · [mcpware/instagram-mcp](https://github.com/mcpware/instagram-mcp) · [mikusnuz/meta-mcp](https://github.com/mikusnuz/meta-mcp)
- [Wh1isper/mcp-email-server](https://github.com/Wh1isper/mcp-email-server) · [n24q02m/better-email-mcp](https://github.com/n24q02m/better-email-mcp)
