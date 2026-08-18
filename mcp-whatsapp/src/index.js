#!/usr/bin/env node

const path = require("path");
const fs = require("fs");
const http = require("http");
const https = require("https");
const { URL } = require("url");
const QRCode = require("qrcode");
const { Server } = require("@modelcontextprotocol/sdk/server/index.js");
const {
  StdioServerTransport,
} = require("@modelcontextprotocol/sdk/server/stdio.js");
const {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} = require("@modelcontextprotocol/sdk/types.js");

// Protect stdout — MCP protocol uses it
console.log = (...args) => process.stderr.write(args.join(" ") + "\n");
console.info = console.log;
console.warn = (...args) => process.stderr.write("WARN: " + args.join(" ") + "\n");

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const DATA_DIR =
  process.env.WHATSAPP_DATA_DIR ||
  path.join(__dirname, "..", "..", "data", "whatsapp_sessions");

fs.mkdirSync(DATA_DIR, { recursive: true });

const AUTH_DIR = path.join(DATA_DIR, "baileys_auth");
fs.mkdirSync(AUTH_DIR, { recursive: true });

// Downloaded media (voice notes, photos, documents) land here so the agent
// can forward them (Telegram push, in-app, email attachment, ...).
const MEDIA_DIR = path.join(DATA_DIR, "media");
fs.mkdirSync(MEDIA_DIR, { recursive: true });

const {
  describeMedia,
  extForMime,
  mimeForPath,
  buildSendPayload,
  isNonContentMessage,
  canonicalChatJid,
} = require("./media.js");

const LOG_PATH = path.join(DATA_DIR, "whatsapp-mcp.log");
const log = (msg) => {
  const line = `[${new Date().toISOString()}] ${msg}\n`;
  process.stderr.write(line);
  try { fs.appendFileSync(LOG_PATH, line); } catch (_) {}
};

// ---------------------------------------------------------------------------
// Unified contact store bridge (gateway → encrypted person store)
// ---------------------------------------------------------------------------
// Set by lazyclaw.mcp.manager when spawning this MCP. When absent (e.g. running
// standalone for local debug) the bridge silently no-ops and we fall back to
// the Baileys-synced cache only.

const BRIDGE_URL = process.env.LAZYCLAW_GATEWAY_URL || "";
const BRIDGE_TOKEN = process.env.LAZYCLAW_INTERNAL_TOKEN || "";
const BRIDGE_USER = process.env.LAZYCLAW_USER_ID || "";
const BRIDGE_ENABLED = !!(BRIDGE_URL && BRIDGE_TOKEN && BRIDGE_USER);

if (BRIDGE_ENABLED) {
  log(`Unified-contact bridge: ${BRIDGE_URL} (user ${BRIDGE_USER})`);
} else {
  log("Unified-contact bridge disabled (missing LAZYCLAW_GATEWAY_URL / LAZYCLAW_INTERNAL_TOKEN / LAZYCLAW_USER_ID)");
}

/** Call /api/internal/contacts/search on the gateway. Returns [] on any error. */
function fetchUnifiedContacts(query, limit) {
  return new Promise((resolve) => {
    if (!BRIDGE_ENABLED || !query) return resolve([]);
    let parsed;
    try {
      parsed = new URL(BRIDGE_URL);
    } catch (_) {
      return resolve([]);
    }
    const params = new URLSearchParams({
      user_id: BRIDGE_USER,
      query: String(query),
      limit: String(limit || 5),
    });
    const opts = {
      method: "GET",
      hostname: parsed.hostname,
      port: parsed.port || (parsed.protocol === "https:" ? 443 : 80),
      path: `/api/internal/contacts/search?${params.toString()}`,
      headers: {
        "X-Internal-Token": BRIDGE_TOKEN,
        "Accept": "application/json",
      },
      timeout: 1500,
    };
    const lib = parsed.protocol === "https:" ? https : http;
    const req = lib.request(opts, (res) => {
      let body = "";
      res.on("data", (chunk) => { body += chunk.toString(); });
      res.on("end", () => {
        if (res.statusCode !== 200) {
          log(`bridge contacts/search → HTTP ${res.statusCode}`);
          return resolve([]);
        }
        try {
          const data = JSON.parse(body);
          resolve(Array.isArray(data.matches) ? data.matches : []);
        } catch (e) {
          log(`bridge contacts/search → parse error: ${e.message}`);
          resolve([]);
        }
      });
    });
    req.on("timeout", () => { req.destroy(new Error("timeout")); });
    req.on("error", (e) => {
      log(`bridge contacts/search → ${e.message}`);
      resolve([]);
    });
    req.end();
  });
}

/**
 * Reduce unified-store matches to a single (jid, name) for whatsapp_send.
 * Picks the first match with a `whatsapp_jid` or `phone` handle. Returns null
 * if zero matches OR if 2+ matches share the same name (ambiguous → caller
 * should ask the user; we don't guess here either).
 */
function _pickJidFromUnified(matches) {
  if (!Array.isArray(matches) || matches.length === 0) return null;
  // If multiple distinct people, refuse — caller surfaces an ambiguity error.
  const distinctNames = new Set(matches.map((m) => (m.name || "").trim().toLowerCase()));
  if (distinctNames.size > 1) return null;

  for (const m of matches) {
    const handles = m.handles || {};
    if (handles.whatsapp_jid) {
      return { jid: handles.whatsapp_jid, name: m.name };
    }
    const phones = handles.phone || [];
    const phone = Array.isArray(phones) ? phones[0] : phones;
    if (phone) {
      return { jid: formatJid(phone), name: m.name };
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let sock = null;
let isReady = false;
let latestQR = null;
const contacts = new Map(); // jid → { name, phone, notify }
const messageStore = new Map(); // jid → [{ key, message, messageTimestamp, pushName }]
const mutedChats = new Map(); // jid → mute expiry timestamp (-1 = forever, >0 = until epoch seconds)
const MAX_MESSAGES_PER_CHAT = 100;

// LID ↔ phone mapping — built from contacts + messages
// lid JID → phone JID (e.g. "133913422909630@lid" → "34604246401@s.whatsapp.net")
const lidToPhone = new Map();
const phoneToLid = new Map();

const CONTACTS_FILE = path.join(DATA_DIR, "contacts.json");
const MESSAGES_FILE = path.join(DATA_DIR, "messages.json");
const MUTED_FILE = path.join(DATA_DIR, "muted.json");

// Debounce message saves — avoid disk thrashing on burst of messages
let _msgSaveTimer = null;
let _muteSaveTimer = null;

/**
 * Safely convert Baileys muteEndTime to a JS number.
 * Handles: plain number, Long object ({low, high, unsigned}), string, null.
 * Returns: -1 (muted forever), >0 (epoch seconds), 0 (not muted).
 *
 * Baileys protobuf uses uint64 for muteEndTime.
 * "Muted forever" = -1 signed → 0xFFFFFFFFFFFFFFFF unsigned → huge number.
 * We normalize: anything > year 2100 (epoch 4102444800) → -1 (forever).
 */
function toMuteNumber(val) {
  if (val == null) return 0;
  // Long object from protobufjs
  if (typeof val === "object" && "low" in val && "high" in val) {
    // If high bits are set to 0xFFFFFFFF, it's -1 as uint64 → muted forever
    if ((val.high >>> 0) === 0xFFFFFFFF) return -1;
    // Use toNumber() if available (Long.js)
    if (typeof val.toNumber === "function") {
      const n = val.toNumber();
      return n > 4102444800 ? -1 : n;
    }
    // Manual: high * 2^32 + low (unsigned)
    const n = (val.high >>> 0) * 4294967296 + (val.low >>> 0);
    return n > 4102444800 ? -1 : n;
  }
  const n = Number(val);
  if (isNaN(n)) return 0;
  // Normalize huge values (uint64 wrap of -1) to -1
  if (n > 4102444800) return -1;
  return n;
}

function saveContacts() {
  try {
    const obj = Object.fromEntries(contacts);
    fs.writeFileSync(CONTACTS_FILE, JSON.stringify(obj));
  } catch (_) {}
}

function saveMessages() {
  try {
    const obj = {};
    for (const [jid, msgs] of messageStore) {
      // Only save last 20 per chat to keep file small
      obj[jid] = msgs.slice(-20);
    }
    fs.writeFileSync(MESSAGES_FILE, JSON.stringify(obj));
  } catch (_) {}
}

function saveMessagesDebounced() {
  if (_msgSaveTimer) clearTimeout(_msgSaveTimer);
  _msgSaveTimer = setTimeout(saveMessages, 2000);
}

function saveMuted() {
  try {
    const obj = Object.fromEntries(mutedChats);
    fs.writeFileSync(MUTED_FILE, JSON.stringify(obj));
  } catch (_) {}
}

function saveMutedDebounced() {
  if (_muteSaveTimer) clearTimeout(_muteSaveTimer);
  _muteSaveTimer = setTimeout(saveMuted, 1000);
}

function loadMuted() {
  try {
    if (fs.existsSync(MUTED_FILE)) {
      const obj = JSON.parse(fs.readFileSync(MUTED_FILE, "utf8"));
      for (const [jid, val] of Object.entries(obj)) {
        const n = toMuteNumber(val);
        if (n !== 0) mutedChats.set(jid, n);
      }
      log(`Loaded ${mutedChats.size} muted chats from cache`);
    }
  } catch (_) {}
}

function loadContacts() {
  try {
    if (fs.existsSync(CONTACTS_FILE)) {
      const obj = JSON.parse(fs.readFileSync(CONTACTS_FILE, "utf8"));
      for (const [jid, c] of Object.entries(obj)) {
        // Normalize phone: only real phone numbers (@s.whatsapp.net), not LID numbers
        c.phone = extractPhone(jid);
        contacts.set(jid, c);
      }
      log(`Loaded ${contacts.size} contacts from cache`);
      _buildLidMap();
    }
  } catch (_) {}
}

function loadMessages() {
  try {
    if (fs.existsSync(MESSAGES_FILE)) {
      const obj = JSON.parse(fs.readFileSync(MESSAGES_FILE, "utf8"));
      let total = 0;
      for (const [jid, msgs] of Object.entries(obj)) {
        if (Array.isArray(msgs) && msgs.length > 0) {
          messageStore.set(jid, msgs);
          total += msgs.length;
        }
      }
      log(`Loaded ${total} messages across ${messageStore.size} chats from cache`);
    }
  } catch (_) {}
}

// ---------------------------------------------------------------------------
// LID ↔ Phone mapping
// ---------------------------------------------------------------------------

/** Build LID↔phone map from contacts that share the same name. */
function _buildLidMap() {
  // Group contacts by name
  const byName = new Map(); // name → [jid, ...]
  for (const [jid, c] of contacts) {
    const name = (c.name || c.notify || "").toLowerCase().trim();
    if (!name) continue;
    if (!byName.has(name)) byName.set(name, []);
    byName.get(name).push(jid);
  }
  // For groups with exactly 1 phone + 1 lid, link them
  for (const [, jids] of byName) {
    const phones = jids.filter((j) => j.endsWith("@s.whatsapp.net"));
    const lids = jids.filter((j) => j.endsWith("@lid"));
    if (phones.length === 1 && lids.length === 1) {
      lidToPhone.set(lids[0], phones[0]);
      phoneToLid.set(phones[0], lids[0]);
    }
  }
  if (lidToPhone.size > 0) {
    log(`LID map: ${lidToPhone.size} linked pairs`);
  }
}

/** Get ALL JID variants for a contact (phone + lid). */
function _allJids(jid) {
  const variants = [jid];
  if (jid.endsWith("@lid") && lidToPhone.has(jid)) {
    variants.push(lidToPhone.get(jid));
  } else if (jid.endsWith("@s.whatsapp.net") && phoneToLid.has(jid)) {
    variants.push(phoneToLid.get(jid));
  }
  return variants;
}

/** Get all messages for a contact across all JID variants. */
function _getMessages(jid) {
  const allJids = _allJids(jid);
  const allMsgs = [];
  for (const j of allJids) {
    const msgs = messageStore.get(j);
    if (msgs && msgs.length > 0) allMsgs.push(...msgs);
  }
  // Also brute-force: check if phone digits match any JID
  if (allMsgs.length === 0) {
    const phone = jid.split("@")[0].split(":")[0];
    if (/^\d{7,}$/.test(phone)) {
      for (const [storeJid, msgs] of messageStore) {
        if (storeJid.split("@")[0].split(":")[0] === phone && msgs.length > 0) {
          allMsgs.push(...msgs);
          break;
        }
      }
    }
  }
  // Dedup by message ID
  const seen = new Set();
  return allMsgs.filter((m) => {
    const id = m.key?.id;
    if (!id || seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

// ---------------------------------------------------------------------------
// Baileys auth state (file-based persistence)
// ---------------------------------------------------------------------------

async function loadAuthState() {
  const { useMultiFileAuthState } = require("@whiskeysockets/baileys");
  return useMultiFileAuthState(AUTH_DIR);
}

// ---------------------------------------------------------------------------
// WhatsApp connection via Baileys
// ---------------------------------------------------------------------------

// Lock file to prevent multiple processes connecting with same auth
const LOCK_FILE = path.join(DATA_DIR, "whatsapp.lock");
const { lockDecision } = require("./lock.js");

// Hostname identifies the container incarnation (Docker sets it to the
// container id, new on every recreate). Written into the lock so a rebuild
// can't strand us cache-only on a PID that collided across PID namespaces
// (2026-08-18: fresh-looking lock from the previous container + PID reuse
// → sends failed "WhatsApp not connected" until manual reconnect).
const LOCK_HOSTNAME = require("os").hostname();

// True only after WE acquired (or force-took) the lock. The 30s refresh
// below must not run for a cache-only loser — refreshing unconditionally
// made the loser clobber the real holder's pid/heartbeat (last-writer-wins
// oscillation between the two processes).
let _ownsLock = false;

function _writeLock() {
  fs.writeFileSync(LOCK_FILE, JSON.stringify({
    pid: process.pid, time: Date.now(), hostname: LOCK_HOSTNAME,
  }));
}

function acquireLock() {
  try {
    if (fs.existsSync(LOCK_FILE)) {
      const lockData = JSON.parse(fs.readFileSync(LOCK_FILE, "utf8"));
      const decision = lockDecision(lockData, {
        now: Date.now(),
        pid: process.pid,
        hostname: LOCK_HOSTNAME,
        isPidAlive: (pid) => {
          try { process.kill(pid, 0); return true; } catch (_) { return false; }
        },
      });
      if (!decision.takeover) {
        return false; // Legitimate process holds the lock
      }
      if (decision.reason !== "no-lock") {
        log(`Lock takeover (${decision.reason}) — previous holder pid=${lockData.pid} host=${lockData.hostname || "?"}`);
      }
    }
    _writeLock();
    _ownsLock = true;
    return true;
  } catch (_) { _ownsLock = true; return true; }
}

function releaseLock() {
  _ownsLock = false;
  try { fs.unlinkSync(LOCK_FILE); } catch (_) {}
}

// Refresh lock periodically — holders only (see _ownsLock).
setInterval(() => {
  if (!_ownsLock) return;
  try { _writeLock(); } catch (_) {}
}, 30000);

process.on("exit", releaseLock);
process.on("SIGINT", () => { releaseLock(); process.exit(0); });
process.on("SIGTERM", () => { releaseLock(); process.exit(0); });

/** Kill any orphaned node processes running this same script (not us). */
function killOrphanedProcesses() {
  let killed = false;
  try {
    const { execSync } = require("child_process");
    const scriptPath = "mcp-whatsapp/src/index.js";
    // Find all node processes running this script
    const out = execSync(`pgrep -f "${scriptPath}" 2>/dev/null || true`, { timeout: 3000 }).toString().trim();
    for (const line of out.split("\n")) {
      const pid = parseInt(line.trim(), 10);
      if (pid && pid !== process.pid) {
        log(`Killing orphaned WhatsApp MCP process (pid=${pid})`);
        try { process.kill(pid, "SIGKILL"); killed = true; } catch (_) {}
      }
    }
  } catch (_) { /* pgrep not available or failed — skip */ }
  // After killing orphans, remove stale lock so we can acquire it
  if (killed) {
    try { fs.unlinkSync(LOCK_FILE); } catch (_) {}
  }
}

let _muteResyncTimer = null;

async function startWhatsApp(force = false) {
  // Clean up any orphaned instances of this script first
  killOrphanedProcesses();
  // Prevent 440 loop: only one process should connect at a time
  if (!force && !acquireLock()) {
    log("Another WhatsApp MCP process holds the lock — running cache-only (no new connection)");
    // Don't take over — serve from cached messages/contacts instead.
    // This prevents the 440 disconnect loop where each new process
    // kills the previous WhatsApp connection.
    return;
  }
  if (force) {
    // User explicitly requested reconnect — take over the lock
    releaseLock();
    _writeLock();
    _ownsLock = true;
  }

  const {
    default: makeWASocket,
    DisconnectReason,
    fetchLatestBaileysVersion,
    makeCacheableSignalKeyStore,
  } = require("@whiskeysockets/baileys");

  const { state, saveCreds } = await loadAuthState();
  const { version } = await fetchLatestBaileysVersion();
  const silentLogger = { trace() {}, debug() {}, info() {}, warn() {}, error() {}, fatal() {}, child() { return this; }, level: 'silent' };

  sock = makeWASocket({
    version,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, silentLogger),
    },
    printQRInTerminal: false,
    browser: ["LazyClaw", "Desktop", "1.0.0"],
    generateHighQualityLinkPreview: false,
    syncFullHistory: true,
    shouldSyncHistoryMessage: () => true,
    logger: silentLogger,
  });

  // Save credentials on update
  sock.ev.on("creds.update", saveCreds);

  // Connection updates
  sock.ev.on("connection.update", (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      latestQR = qr;
      log("QR code received");

      // Send QR to Telegram as image
      const telegramToken = process.env.TELEGRAM_BOT_TOKEN;
      const telegramChat = process.env.TELEGRAM_ADMIN_CHAT;
      log(`QR event — token=${telegramToken ? "yes" : "MISSING"} chat=${telegramChat || "MISSING"}`);

      if (telegramToken && telegramChat) {
        QRCode.toBuffer(qr, { width: 512, margin: 2 })
          .then((pngBuffer) => {
            const boundary = "----QRBoundary" + Date.now();
            const CRLF = "\r\n";
            const head = Buffer.from(
              `--${boundary}${CRLF}` +
              `Content-Disposition: form-data; name="chat_id"${CRLF}${CRLF}` +
              `${telegramChat}${CRLF}` +
              `--${boundary}${CRLF}` +
              `Content-Disposition: form-data; name="caption"${CRLF}${CRLF}` +
              `WhatsApp QR — scan within 60s${CRLF}` +
              `--${boundary}${CRLF}` +
              `Content-Disposition: form-data; name="photo"; filename="qr.png"${CRLF}` +
              `Content-Type: image/png${CRLF}${CRLF}`
            );
            const tail = Buffer.from(`${CRLF}--${boundary}--${CRLF}`);
            const body = Buffer.concat([head, pngBuffer, tail]);

            const req = https.request(
              {
                hostname: "api.telegram.org",
                path: `/bot${telegramToken}/sendPhoto`,
                method: "POST",
                headers: {
                  "Content-Type": `multipart/form-data; boundary=${boundary}`,
                  "Content-Length": body.length,
                },
              },
              (res) => {
                let data = "";
                res.on("data", (chunk) => { data += chunk; });
                res.on("end", () => {
                  if (res.statusCode !== 200) {
                    log(`Telegram sendPhoto FAILED (${res.statusCode}): ${data}`);
                  } else {
                    log("Telegram QR photo sent OK");
                  }
                });
              }
            );
            req.on("error", (e) => { log(`Telegram QR send error: ${e.message}`); });
            req.end(body);
          })
          .catch((e) => { log(`QR image generation failed: ${e.message}`); });
      } else {
        log("Skipping Telegram — missing token or chat_id");
      }
    }

    if (connection === "open") {
      isReady = true;
      latestQR = null;
      const me = sock.user;
      log(`WhatsApp connected as ${me ? me.id : "unknown"}`);
      log(`Contacts cached: ${contacts.size}`);

      // Log contact + message count after history sync settles
      setTimeout(async () => {
        const totalMsgs = [...messageStore.values()].reduce((sum, msgs) => sum + msgs.length, 0);
        const lidCount = [...contacts.keys()].filter((k) => k.endsWith("@lid")).length;
        const phoneCount = [...contacts.keys()].filter((k) => k.endsWith("@s.whatsapp.net")).length;
        log(`After connect: ${contacts.size} contacts (${phoneCount} phone, ${lidCount} LID), ${totalMsgs} messages cached`);
        _buildLidMap();

        // Eager group-metadata hydration: one bulk call fetches all groups' subjects,
        // participants, and mute state. Fixes "Group" display + missing sender names.
        try {
          const allGroups = await sock.groupFetchAllParticipating();
          let hydrated = 0;
          let participantsAdded = 0;
          for (const [gjid, meta] of Object.entries(allGroups || {})) {
            const existing = contacts.get(gjid) || { phone: null, notify: "" };
            if (meta?.subject) existing.name = meta.subject;
            contacts.set(gjid, existing);
            hydrated++;

            for (const p of meta?.participants || []) {
              const pjid = p.id;
              if (!pjid) continue;
              if (!contacts.has(pjid)) {
                contacts.set(pjid, {
                  name: p.notify || p.name || "",
                  notify: p.notify || "",
                  phone: extractPhone(pjid),
                });
                participantsAdded++;
              } else if (!contacts.get(pjid).name && (p.notify || p.name)) {
                contacts.get(pjid).name = p.notify || p.name;
              }
            }

            const muteVal = toMuteNumber(meta?.muteEndTime ?? meta?.announceMuteEndTime ?? null);
            if (muteVal !== 0) mutedChats.set(gjid, muteVal);
          }
          if (hydrated > 0) {
            saveContacts();
            saveMutedDebounced();
            _buildLidMap();
            log(`Group hydration: ${hydrated} groups, +${participantsAdded} participant contacts`);
          }
        } catch (e) {
          log(`groupFetchAllParticipating failed: ${e.message}`);
          // Fallback: old per-group lazy fetch
          let groupsToResolve = 0;
          for (const jid of contacts.keys()) {
            if (jid.endsWith("@g.us") && !contacts.get(jid)?.name) {
              _resolveGroupName(jid);
              groupsToResolve++;
            }
          }
          if (groupsToResolve > 0) log(`Fallback: resolving ${groupsToResolve} group names lazily`);
        }

        // Force app-state resync so mutes applied on the phone arrive here.
        // regular_high carries per-chat mute state; events flow through chats.update.
        try {
          await sock.resyncAppState(["regular_high", "regular_low", "critical_block"], false);
          log("App state resync complete (mute state refreshed)");
        } catch (e) {
          log(`resyncAppState failed: ${e.message}`);
        }

        // Log muted chats for debugging
        if (mutedChats.size > 0) {
          const mutedNames = [...mutedChats.keys()].map((jid) => {
            const name = contacts.get(jid)?.name || jid;
            const val = mutedChats.get(jid);
            return `${name} (${val === -1 ? "forever" : "until " + new Date(val * 1000).toISOString().slice(0, 16)})`;
          });
          log(`Muted chats (${mutedChats.size}): ${mutedNames.join(", ")}`);
        }
      }, 10000);
    }

    if (connection === "close") {
      isReady = false;
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
      log(`WhatsApp disconnected (code=${statusCode}, reconnect=${shouldReconnect})`);

      // Code 440 = connection replaced by another device/session.
      // Do NOT auto-reconnect — causes reconnect war. Wait for user to call whatsapp_setup.
      if (statusCode === 440) {
        log("Connection replaced (code=440). Stopped. Call whatsapp_setup to reconnect.");
        releaseLock();
        sock = null;
        // KEEP isReady = false but DON'T clear contacts/messages.
        // Tools can still return cached data even when disconnected.
      } else if (statusCode === DisconnectReason.loggedOut) {
        log("Logged out — clearing auth for fresh QR on next setup");
        try {
          fs.rmSync(AUTH_DIR, { recursive: true, force: true });
          fs.mkdirSync(AUTH_DIR, { recursive: true });
        } catch (e) {
          log("Failed to clear auth dir: " + e.message);
        }
        sock = null;
      } else if (shouldReconnect) {
        setTimeout(() => startWhatsApp(), 3000);
      }
    }
  });

  // PRIMARY: Full history sync — this is where contacts come from
  let _historyDumpDone = false;
  sock.ev.on("messaging-history.set", (event) => {
    const before = contacts.size;

    // One-time dump: log first 3 group chats with ALL their fields for mute debugging
    if (!_historyDumpDone && (event.chats || []).length > 0) {
      _historyDumpDone = true;
      const groups = (event.chats || []).filter((c) => c.id?.endsWith("@g.us")).slice(0, 3);
      for (const g of groups) {
        const fields = Object.keys(g).filter((k) => g[k] != null && k !== "messages");
        log(`[MUTE-DEBUG] Group ${g.name || g.id} fields: ${fields.join(", ")}`);
        if ("muteEndTime" in g) log(`[MUTE-DEBUG]   muteEndTime = ${JSON.stringify(g.muteEndTime)} (type: ${typeof g.muteEndTime})`);
        if ("muteExpiration" in g) log(`[MUTE-DEBUG]   muteExpiration = ${JSON.stringify(g.muteExpiration)}`);
        if ("mute" in g) log(`[MUTE-DEBUG]   mute = ${JSON.stringify(g.mute)}`);
      }
    }
    for (const c of (event.contacts || [])) {
      const cJid = c.jid || c.id || "";
      contacts.set(c.id, {
        name: c.name || c.verifiedName || c.notify || "",
        notify: c.notify || "",
        phone: extractPhone(cJid),
      });
    }
    // Also extract contacts from chats + track mute status
    for (const chat of (event.chats || [])) {
      if (!contacts.has(chat.id)) {
        const isGroupChat = chat.id.endsWith("@g.us");
        contacts.set(chat.id, {
          // For groups: use chat.name only (don't fallback to phone/JID extraction)
          name: isGroupChat ? (chat.name || "") : (chat.name || extractPhone(chat.id) || chat.id.split("@")[0]),
          notify: "",
          phone: extractPhone(chat.id),
        });
        // Trigger group name fetch if we have no name
        if (isGroupChat && !chat.name) {
          _resolveGroupName(chat.id);
        }
      } else if (chat.name && !contacts.get(chat.id).name) {
        // Update existing contact that has no name yet (e.g. group first seen via messages.upsert)
        contacts.get(chat.id).name = chat.name;
      }
      // Track mute — Baileys protobuf field is "muteEndTime" (uint64)
      // Also check all known mute field variants for safety
      const rawMute = chat.muteEndTime ?? chat.muteExpiration ?? chat.mute ?? null;
      const muteVal = toMuteNumber(rawMute);
      if (muteVal !== 0) {
        mutedChats.set(chat.id, muteVal);
        // Log raw value for debugging mute sync issues
        const chatLabel = chat.name || chat.id;
        log(`Mute detected (history): ${chatLabel} raw=${JSON.stringify(rawMute)} → ${muteVal}`);
      }
    }
    if (mutedChats.size > 0) {
      log(`Muted chats after history sync: ${mutedChats.size}`);
      saveMutedDebounced();
    }
    const added = contacts.size - before;
    if (added > 0) {
      saveContacts();
      _buildLidMap();
      log(`History sync: +${added} contacts (${contacts.size} total)`);
    }
  });

  // Incremental contact updates
  sock.ev.on("contacts.upsert", (newContacts) => {
    for (const c of newContacts) {
      contacts.set(c.id, {
        name: c.name || c.verifiedName || c.notify || "",
        notify: c.notify || "",
        phone: extractPhone(c.id),
      });
    }
    log(`Contacts upsert: ${contacts.size} total`);
    saveContacts();
    _buildLidMap();
  });

  sock.ev.on("contacts.update", (updates) => {
    for (const u of updates) {
      const existing = contacts.get(u.id) || { phone: extractPhone(u.id) };
      if (u.name) existing.name = u.name;
      if (u.notify) existing.notify = u.notify;
      contacts.set(u.id, existing);
    }
    saveContacts();
  });

  // Track chats for list_chats
  sock.ev.on("chats.upsert", (chats) => {
    for (const chat of chats) {
      const jid = chat.id;
      if (!contacts.has(jid)) {
        const isGroupChat = jid.endsWith("@g.us");
        contacts.set(jid, {
          name: isGroupChat ? (chat.name || "") : (chat.name || extractPhone(jid) || jid.split("@")[0]),
          phone: extractPhone(jid),
        });
        if (isGroupChat && !chat.name) {
          _resolveGroupName(jid);
        }
      } else if (chat.name && !contacts.get(jid).name) {
        // Update existing contact that has no name yet
        contacts.get(jid).name = chat.name;
      }
      const rawUpsertMute = chat.muteEndTime ?? chat.muteExpiration ?? chat.mute ?? null;
      const chatMuteVal = toMuteNumber(rawUpsertMute);
      if (chatMuteVal !== 0) {
        mutedChats.set(jid, chatMuteVal);
        log(`Mute detected (upsert): ${chat.name || jid} raw=${JSON.stringify(rawUpsertMute)} → ${chatMuteVal}`);
        saveMutedDebounced();
      }
    }
  });

  // Track mute/unmute changes (from app state sync + real-time user actions)
  sock.ev.on("chats.update", (updates) => {
    let muteChanged = false;
    for (const u of updates) {
      if (!u.id) continue;
      // Update name if provided
      if (u.name && contacts.has(u.id)) {
        contacts.get(u.id).name = u.name;
      }
      // Update mute status — check all known Baileys mute field names
      // App state sync uses muteEndTime, some versions use muteExpiration
      const hasMuteField = "muteEndTime" in u || "muteExpiration" in u || "mute" in u;
      if (hasMuteField) {
        const rawUpdateMute = u.muteEndTime ?? u.muteExpiration ?? u.mute ?? null;
        const muteVal = toMuteNumber(rawUpdateMute);
        const chatName = contacts.get(u.id)?.name || u.id;
        if (muteVal === 0) {
          mutedChats.delete(u.id);
          log(`Mute updated: ${chatName} → unmuted`);
        } else {
          mutedChats.set(u.id, muteVal);
          log(`Mute updated: ${chatName} → muted (${muteVal === -1 ? "forever" : "until " + new Date(muteVal * 1000).toISOString()})`);
        }
        muteChanged = true;
      }
    }
    if (muteChanged) saveMutedDebounced();
  });

  // Track message senders + store messages for reading
  sock.ev.on("messages.upsert", ({ messages: msgs, type }) => {
    if (msgs.length > 0) {
      log(`messages.upsert: ${msgs.length} messages (type=${type})`);
    }
    for (const msg of msgs) {
      const jid = msg.key.remoteJid;
      if (!jid || jid === "status@broadcast") continue;

      // Update contact info — NEVER overwrite group names with a participant's pushName
      const isGroupJid = jid.endsWith("@g.us");
      if (!contacts.has(jid)) {
        if (isGroupJid) {
          // For groups: don't use pushName (that's the sender, not the group)
          // Trigger lazy fetch instead
          contacts.set(jid, {
            name: "",
            notify: "",
            phone: null,
          });
          _resolveGroupName(jid);
        } else {
          contacts.set(jid, {
            name: msg.pushName || extractPhone(jid) || jid.split("@")[0],
            notify: msg.pushName || "",
            phone: extractPhone(jid),
          });
        }
      } else if (msg.pushName && !isGroupJid) {
        // Only update name from pushName for direct chats, never for groups
        const c = contacts.get(jid);
        if (!c.name || c.name === c.phone) c.name = msg.pushName;
        c.notify = msg.pushName;
      }

      // Store message for later reading
      if (!messageStore.has(jid)) messageStore.set(jid, []);
      const chatMsgs = messageStore.get(jid);
      // Avoid duplicates (same message ID)
      const msgId = msg.key.id;
      if (!chatMsgs.some((m) => m.key.id === msgId)) {
        chatMsgs.push(msg);
        // Keep only most recent messages per chat
        if (chatMsgs.length > MAX_MESSAGES_PER_CHAT) {
          chatMsgs.splice(0, chatMsgs.length - MAX_MESSAGES_PER_CHAT);
        }
      }
    }
    saveMessagesDebounced();
  });

  // Also store messages from history sync
  sock.ev.on("messaging-history.set", (event) => {
    const msgCount = (event.messages || []).length;
    const chatCount = (event.chats || []).length;
    const contactCount = (event.contacts || []).length;
    log(`History sync event: ${msgCount} messages, ${chatCount} chats, ${contactCount} contacts, isLatest=${event.isLatest}`);
    let dmNamesLearned = 0;
    for (const msg of (event.messages || [])) {
      const jid = msg.key?.remoteJid;
      if (!jid || jid === "status@broadcast") continue;
      if (!messageStore.has(jid)) messageStore.set(jid, []);
      const chatMsgs = messageStore.get(jid);
      const msgId = msg.key.id;
      if (!chatMsgs.some((m) => m.key.id === msgId)) {
        chatMsgs.push(msg);
      }

      // Learn DM contact names from history-sync pushName.
      // Without this, old DMs stay stuck as "+<phone>" since messages.upsert only
      // fires for new messages — historical ones never update the contact map.
      const isDmJid = jid.endsWith("@s.whatsapp.net");
      if (isDmJid && msg.pushName && !msg.key.fromMe) {
        const existing = contacts.get(jid);
        const phone = extractPhone(jid);
        const needsName = !existing || !existing.name || existing.name === existing.phone || existing.name === phone;
        if (needsName) {
          contacts.set(jid, {
            name: msg.pushName,
            notify: msg.pushName,
            phone: phone,
          });
          dmNamesLearned++;
        } else if (existing && !existing.notify) {
          existing.notify = msg.pushName;
        }
      }
    }
    // Trim all chats after history sync
    for (const [, msgs] of messageStore) {
      if (msgs.length > MAX_MESSAGES_PER_CHAT) {
        msgs.splice(0, msgs.length - MAX_MESSAGES_PER_CHAT);
      }
    }
    const totalStored = [...messageStore.values()].reduce((s, m) => s + m.length, 0);
    if (msgCount > 0) {
      log(`After history sync: ${totalStored} messages stored across ${messageStore.size} chats`);
      saveMessagesDebounced();
    }
    if (dmNamesLearned > 0) {
      log(`Learned ${dmNamesLearned} DM contact names from history pushName`);
      saveContacts();
    }
  });

  // Periodic mute-state resync so phone-side mute toggles reach the MCP
  // even without a new message arriving. Installed once per process.
  if (!_muteResyncTimer) {
    _muteResyncTimer = setInterval(() => {
      if (isReady && sock) {
        sock.resyncAppState(["regular_high"], false).catch(() => {});
      }
    }, 10 * 60 * 1000);
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Pending group name fetches to avoid hammering Baileys
const _pendingGroupFetches = new Set();

/** Lazily fetch group name via groupMetadata if missing. Fire-and-forget. */
function _resolveGroupName(jid) {
  if (!jid.endsWith("@g.us") || !sock || !isReady) return;
  const c = contacts.get(jid);
  if (c && c.name) return; // Already have a name
  if (_pendingGroupFetches.has(jid)) return; // Already fetching
  _pendingGroupFetches.add(jid);
  sock.groupMetadata(jid).then((meta) => {
    if (meta && meta.subject) {
      const existing = contacts.get(jid) || { phone: null, notify: "" };
      existing.name = meta.subject;
      contacts.set(jid, existing);
      saveContacts();
      log(`Resolved group name: ${jid} → "${meta.subject}"`);
    }
  }).catch(() => {}).finally(() => _pendingGroupFetches.delete(jid));
}

/**
 * Resolve a DM contact's display name by scanning cached messages for a pushName.
 * Baileys doesn't expose an on-demand name-lookup API, but history-synced
 * messages usually carry `msg.pushName`. This catches DMs whose contacts entry
 * was created before we started learning names from history (existing message cache).
 */
function _resolveContactName(jid) {
  if (!jid || !jid.endsWith("@s.whatsapp.net")) return;
  const existing = contacts.get(jid);
  const phone = extractPhone(jid);
  if (existing && existing.name && existing.name !== existing.phone && existing.name !== phone) return;
  const msgs = messageStore.get(jid);
  if (!msgs || msgs.length === 0) return;
  for (const m of msgs) {
    if (m.key?.fromMe) continue;
    if (m.pushName) {
      const next = existing || { phone, notify: "" };
      next.name = m.pushName;
      if (!next.notify) next.notify = m.pushName;
      contacts.set(jid, next);
      saveContacts();
      log(`Resolved DM name from cache: ${jid} → "${m.pushName}"`);
      return;
    }
  }
}

/** Check if a chat is currently muted. */
function _isChatMuted(jid) {
  // Normalize JID before lookup — message remoteJid may include session suffix
  // (e.g. "15551234567:18@s.whatsapp.net") while mutedChats keys are normalized
  const normalizedJid = normalizeJid(jid);
  const muteExpiry = mutedChats.get(normalizedJid);
  if (muteExpiry == null) return false;
  if (muteExpiry === -1) return true; // muted forever
  if (muteExpiry > 0) return muteExpiry > Date.now() / 1000; // muted until timestamp
  return false; // 0 = not muted
}

function formatJid(phone) {
  return phone.replace(/[^0-9]/g, "") + "@s.whatsapp.net";
}

/** Extract phone number from JID. Returns "+NNNN" for phone JIDs, null for LID/group. */
function extractPhone(jid) {
  if (!jid || !jid.endsWith("@s.whatsapp.net")) return null;
  const num = jid.split("@")[0].split(":")[0];
  return num ? "+" + num : null;
}

/** Normalize a JID — strip group suffix (:NN) for direct messages. */
function normalizeJid(jid) {
  if (!jid) return jid;
  // "15551234567:18@s.whatsapp.net" → "15551234567@s.whatsapp.net"
  // Group JIDs (@g.us) are left untouched
  if (jid.includes("@s.whatsapp.net")) {
    const phone = jid.split("@")[0].split(":")[0];
    return phone + "@s.whatsapp.net";
  }
  return jid;
}

/** Look up a display name for a JID, walking @lid ↔ @s.whatsapp.net variants. */
function _displayNameForJid(jid) {
  if (!jid) return "";
  for (const variant of _allJids(jid)) {
    const c = contacts.get(variant);
    if (c && (c.name || c.notify)) return c.name || c.notify;
  }
  // Last resort: try normalized form
  const norm = normalizeJid(jid);
  if (norm !== jid) {
    const c = contacts.get(norm);
    if (c && (c.name || c.notify)) return c.name || c.notify;
  }
  return "";
}

/**
 * Tier-based resolver. Returns one of:
 *   { jid, name, source }                           — confident single match
 *   { ambiguous: true, candidates: [{jid,name}], reason: "..." }
 *   null                                            — no match anywhere
 *
 * Tiers (highest to lowest priority):
 *   1. Direct phone digits        → @s.whatsapp.net JID
 *   2. Exact name / notify match  → unique winner; @s.whatsapp.net preferred
 *   3. Starts-with name match     → unique winner only (else AMBIGUOUS)
 *   4. Substring name/notify      → unique winner only (else AMBIGUOUS)
 *
 * Critical invariant: NEVER pick the "first plausible partial" the way the old
 * scoring did. If 3+ contacts contain the substring, the brain MUST ask the
 * user — guessing silently is how the wrong-number bug used to ship.
 */
function resolveContact(query) {
  if (!query) return null;
  const q = String(query).toLowerCase().trim();
  if (!q) return null;

  // Tier 0 — already a JID ("…@s.whatsapp.net" / "…@g.us" / "…@lid"):
  // trust it directly. The unified inbox stores chat JIDs as the thread
  // handle, so reads/sends arrive here with a full JID — name matching
  // would miss it, and the phone tier mangles @lid / group JIDs.
  if (/@(s\.whatsapp\.net|g\.us|lid)$/.test(q)) {
    const raw = String(query).trim();
    // Prefer the phone-JID twin for @lid contacts (sending needs it).
    const jid = raw.endsWith("@lid") && lidToPhone.has(raw)
      ? lidToPhone.get(raw)
      : raw;
    return {
      jid,
      name: _displayNameForJid(jid) || extractPhone(jid) || jid.split("@")[0],
      source: "jid-input",
    };
  }

  // Tier 1 — phone-looking input
  if (/^\+?\d{7,}$/.test(q.replace(/[^0-9+]/g, ""))) {
    const jid = formatJid(q);
    const name = _displayNameForJid(jid) || q;
    return { jid, name, source: "phone-input" };
  }

  // Iterate cache once, classify by tier
  const exact = [];        // name === q (highest)
  const startsWith = [];   // name starts with q
  const substring = [];    // name contains q
  const phoneSubstr = [];  // phone digits contain q (only fires when q has digits)
  const seenJids = new Set();

  const qDigits = q.replace(/[^0-9]/g, "");

  for (const [jid, c] of contacts) {
    const name = (c.name || "").toLowerCase();
    const notify = (c.notify || "").toLowerCase();
    const phone = (c.phone || "").toLowerCase();
    if (!name && !notify && !phone) continue;

    // Prefer the phone-JID twin for sending.
    let canonical = jid;
    if (jid.endsWith("@lid") && lidToPhone.has(jid)) {
      canonical = lidToPhone.get(jid);
    }
    canonical = normalizeJid(canonical);
    if (seenJids.has(canonical)) continue;
    // We don't dedupe yet — keep classifying — but record one canonical per tier hit.

    const display = c.name || c.notify || phone || "";
    const entry = { jid: canonical, name: display };

    if (name === q || notify === q) {
      exact.push(entry);
      seenJids.add(canonical);
      continue;
    }
    if ((name && name.startsWith(q)) || (notify && notify.startsWith(q))) {
      startsWith.push(entry);
      continue;
    }
    if ((name && name.includes(q)) || (notify && notify.includes(q))) {
      substring.push(entry);
      continue;
    }
    if (qDigits && qDigits.length >= 4 && phone && phone.replace(/[^0-9]/g, "").includes(qDigits)) {
      phoneSubstr.push(entry);
    }
  }

  const pickUnique = (pool, reasonLabel) => {
    if (pool.length === 0) return null;
    // De-dupe by canonical JID
    const seen = new Map();
    for (const e of pool) {
      if (!seen.has(e.jid)) seen.set(e.jid, e);
    }
    const unique = [...seen.values()];
    if (unique.length === 1) return { ...unique[0], source: reasonLabel };
    return { ambiguous: true, candidates: unique.slice(0, 5), reason: reasonLabel };
  };

  // Exact wins regardless of count — multiple exact hits = legitimate ambiguity
  if (exact.length > 0) {
    const seen = new Map();
    for (const e of exact) {
      if (!seen.has(e.jid)) seen.set(e.jid, e);
    }
    const unique = [...seen.values()];
    if (unique.length === 1) return { ...unique[0], source: "exact" };
    return { ambiguous: true, candidates: unique.slice(0, 5), reason: "exact-multi" };
  }

  const sw = pickUnique(startsWith, "starts-with");
  if (sw) return sw;

  const sub = pickUnique(substring, "substring");
  if (sub) return sub;

  const ps = pickUnique(phoneSubstr, "phone-substring");
  if (ps) return ps;

  return null;
}

function ok(data) {
  return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
}

function err(message) {
  return { content: [{ type: "text", text: JSON.stringify({ error: message }) }], isError: true };
}

/** Format a message for display. */
function _formatMsg(msg) {
  const body =
    msg.message?.conversation ||
    msg.message?.extendedTextMessage?.text ||
    msg.message?.imageMessage?.caption ||
    msg.message?.videoMessage?.caption ||
    (msg.message?.imageMessage ? "[image]" : null) ||
    (msg.message?.videoMessage ? "[video]" : null) ||
    (msg.message?.audioMessage ? "[audio]" : null) ||
    (msg.message?.documentMessage ? `[document: ${msg.message.documentMessage.fileName || "file"}]` : null) ||
    (msg.message?.stickerMessage ? "[sticker]" : null) ||
    "[media]";
  const ts = Number(msg.messageTimestamp || 0);
  const time = ts > 0 ? new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC" : "unknown";

  const jid = msg.key.remoteJid || "";
  const isGroup = jid.endsWith("@g.us");
  let chatName = (!isGroup ? _displayNameForJid(jid) : contacts.get(jid)?.name || "")
    || (isGroup ? `Group ${jid.split("@")[0].slice(-6)}` : null)
    || extractPhone(jid)
    || jid.split("@")[0];

  // Trigger lazy group name fetch if missing
  if (isGroup && !contacts.get(jid)?.name) {
    _resolveGroupName(jid);
  }
  // Trigger lazy DM name fetch when the cached name is still the phone fallback
  if (!isGroup && jid.endsWith("@s.whatsapp.net")) {
    const dmc = contacts.get(jid);
    const phoneStr = extractPhone(jid);
    if (!dmc || !dmc.name || dmc.name === dmc.phone || dmc.name === phoneStr) {
      _resolveContactName(jid);
    }
  }

  // Check if this chat is muted on WhatsApp
  // Normalized: -1 = muted forever, >0 = muted until epoch seconds, absent/0 = not muted
  const isMuted = _isChatMuted(jid);

  const result = {
    id: msg.key.id,
    from: msg.key.fromMe ? "me" : (msg.pushName || chatName),
    body,
    time,
    fromMe: msg.key.fromMe,
    type: isGroup ? "group" : "direct",
    chatName,
    // Stable chat identifier — `from` is a DISPLAY NAME (pushName) and must
    // never be used as a handle: the unified inbox stored it as the thread
    // contact and later reads/sends failed to resolve it. CANONICALIZED so an
    // @lid JID and its @s.whatsapp.net phone twin (and any ":NN" device
    // suffix) collapse to ONE thread instead of forking duplicates.
    chat_jid: canonicalChatJid(jid, lidToPhone),
    muted: isMuted,
  };

  // Surface media metadata so the agent KNOWS this bubble carries a voice
  // note / photo / file and can fetch the actual bytes on demand via
  // whatsapp_download_media(message_id=id).
  const media = describeMedia(msg);
  if (media) {
    result.media = media;
    result.media_download = `whatsapp_download_media(message_id="${msg.key.id}")`;
  }

  // For group messages: "from" = person who sent, add group context
  if (isGroup && !msg.key.fromMe) {
    const partJid = msg.key.participant || "";
    // Resolve participant name: saved contact first (hydrated via groupFetchAllParticipating),
    // then Baileys notify, then LID→phone bridge, and only fall back to msg.pushName last.
    const partContact = contacts.get(partJid);
    const partName =
         partContact?.name
      || partContact?.notify
      || (lidToPhone.has(partJid) ? contacts.get(lidToPhone.get(partJid))?.name : "")
      || msg.pushName
      || "";
    result.participant = partJid;
    result.participantName = partName;
    result.groupName = chatName;
    result.from = partName || extractPhone(partJid) || partJid.split("@")[0].slice(-8) || chatName;
  }

  return result;
}

// ---------------------------------------------------------------------------
// Tool definitions
// ---------------------------------------------------------------------------

const TOOLS = [
  {
    name: "whatsapp_setup",
    description: "Initialize WhatsApp connection and get QR code. Only call if whatsapp_read/send returns 'not connected'.",
    inputSchema: { type: "object", properties: {}, required: [] },
  },
  {
    name: "whatsapp_status",
    description: "Check WhatsApp connection status. Usually not needed — just call whatsapp_read directly.",
    inputSchema: { type: "object", properties: {}, required: [] },
  },
  {
    name: "whatsapp_send",
    description: "Send a WhatsApp message. Resolves contact by name or phone number.",
    inputSchema: {
      type: "object",
      properties: {
        to: { type: "string", description: 'Contact name or phone number. E.g. "John" or "15551234567"' },
        message: { type: "string", description: "Message text to send" },
      },
      required: ["to", "message"],
    },
  },
  {
    name: "whatsapp_read",
    description: "Read recent WhatsApp messages. Without contact: reads the most recent chat. With contact: reads that specific chat. Works even when disconnected (returns cached data).",
    inputSchema: {
      type: "object",
      properties: {
        contact: { type: "string", description: 'Optional: contact name or phone. Omit to read most recent chat.' },
        limit: { type: "number", description: "Number of messages to fetch (default 10)" },
      },
      required: [],
    },
  },
  {
    name: "whatsapp_list_chats",
    description: "List recent WhatsApp chats with message counts. Includes direct chats AND groups.",
    inputSchema: {
      type: "object",
      properties: {
        limit: { type: "number", description: "Number of chats to return (default 30)" },
        include_groups: { type: "boolean", description: "Include group chats (default true)" },
      },
      required: [],
    },
  },
  {
    name: "whatsapp_search",
    description: "Search WhatsApp contacts by name or number.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Name or phone number to search" },
      },
      required: ["query"],
    },
  },
  {
    name: "whatsapp_send_image",
    description: "Send an image via WhatsApp.",
    inputSchema: {
      type: "object",
      properties: {
        to: { type: "string", description: 'Contact name or phone number. E.g. "John" or "15551234567"' },
        image_path: { type: "string", description: "Absolute path to the image file" },
        caption: { type: "string", description: "Optional caption for the image" },
      },
      required: ["to", "image_path"],
    },
  },
  {
    name: "whatsapp_send_audio",
    description: "Send an audio file via WhatsApp. By default it is sent as a VOICE NOTE (native play bubble). Pass voice_note=false to send as a plain audio file. Best voice-note format: .ogg/.opus; .mp3/.m4a also work.",
    inputSchema: {
      type: "object",
      properties: {
        to: { type: "string", description: 'Contact name or phone number. E.g. "John" or "15551234567"' },
        audio_path: { type: "string", description: "Absolute path to the audio file" },
        voice_note: { type: "boolean", description: "Send as voice note (default true). false = plain audio file attachment." },
      },
      required: ["to", "audio_path"],
    },
  },
  {
    name: "whatsapp_send_video",
    description: "Send a video via WhatsApp.",
    inputSchema: {
      type: "object",
      properties: {
        to: { type: "string", description: 'Contact name or phone number. E.g. "John" or "15551234567"' },
        video_path: { type: "string", description: "Absolute path to the video file (.mp4 recommended)" },
        caption: { type: "string", description: "Optional caption for the video" },
      },
      required: ["to", "video_path"],
    },
  },
  {
    name: "whatsapp_send_document",
    description: "Send a document/file via WhatsApp (PDF, docx, xlsx, zip, ...).",
    inputSchema: {
      type: "object",
      properties: {
        to: { type: "string", description: 'Contact name or phone number. E.g. "John" or "15551234567"' },
        file_path: { type: "string", description: "Absolute path to the file" },
        file_name: { type: "string", description: "Optional display filename (defaults to the file's basename)" },
        caption: { type: "string", description: "Optional caption" },
      },
      required: ["to", "file_path"],
    },
  },
  {
    name: "whatsapp_download_media",
    description: "Download the media (voice note, photo, video, document, sticker) attached to a message. Use the 'id' from whatsapp_read results (messages with a 'media' field). Returns the saved file path so you can forward/play/send it elsewhere.",
    inputSchema: {
      type: "object",
      properties: {
        message_id: { type: "string", description: "Message id from whatsapp_read (messages that have a 'media' field)" },
        contact: { type: "string", description: "Optional: contact/chat name to narrow the search (faster)" },
      },
      required: ["message_id"],
    },
  },
  {
    name: "whatsapp_mute",
    description: "Mute or unmute a chat/group so the watcher skips its messages. Use when user replies 'mute' to a notification or says 'mute that group'. Also syncs mute to WhatsApp app when connected.",
    inputSchema: {
      type: "object",
      properties: {
        chat: { type: "string", description: "Chat/group name or phone number to mute/unmute" },
        action: { type: "string", enum: ["mute", "unmute"], description: "mute or unmute (default: mute)" },
      },
      required: ["chat"],
    },
  },
  {
    name: "whatsapp_list_muted",
    description: "List all muted chats and groups. Shows both WhatsApp-synced and manually muted chats.",
    inputSchema: { type: "object", properties: {}, required: [] },
  },
];

// ---------------------------------------------------------------------------
// Tool handlers
// ---------------------------------------------------------------------------

async function handleSetup() {
  if (isReady && sock) {
    const me = sock.user;
    return ok({ status: "connected", phone: me ? me.id.split(":")[0] : "unknown", contacts: contacts.size });
  }
  // Not connected — start/restart WhatsApp (force = user explicitly asked)
  if (!sock) {
    log("Setup called — starting WhatsApp connection (force)...");
    await startWhatsApp(true);
    // Wait briefly for QR or connection
    await new Promise((r) => setTimeout(r, 3000));
  }
  if (isReady && sock) {
    const me = sock.user;
    return ok({ status: "connected", phone: me ? me.id.split(":")[0] : "unknown", contacts: contacts.size });
  }
  if (latestQR) {
    let qrAscii = "";
    await new Promise((resolve) => {
      QRCode.toString(latestQR, { type: "terminal", small: true }, (e, str) => {
        qrAscii = str || "";
        resolve();
      });
    });
    return ok({ status: "qr_needed", qr: latestQR, qr_ascii: qrAscii, note: "Scan QR in WhatsApp app → Linked Devices" });
  }
  return ok({ status: "initializing", note: "Waiting for QR code..." });
}

async function handleStatus() {
  const totalMsgs = [...messageStore.values()].reduce((sum, msgs) => sum + msgs.length, 0);
  if (isReady && sock) {
    const me = sock.user;
    return ok({ status: "connected", phone: me ? me.id.split(":")[0] : "unknown", contacts: contacts.size, messages_cached: totalMsgs });
  }
  if (latestQR) return ok({ status: "qr_needed" });
  // Disconnected but have cached data
  return ok({ status: "disconnected", contacts_cached: contacts.size, messages_cached: totalMsgs, note: "Read tools still work with cached data. Call whatsapp_setup to reconnect." });
}

async function handleSend(args) {
  if (!isReady || !sock) return err("WhatsApp not connected. Call whatsapp_setup first.");
  const to = args.to || args.phone;
  const message = args.message;
  if (!to || !message) return err("Both 'to' and 'message' are required.");

  const inputLooksLikePhone = /^\+?\d{7,}$/.test(String(to).replace(/[^0-9+]/g, ""));
  let resolved = resolveContact(to);

  // Local-cache miss → try unified contact store before giving up.
  // Brain may also call find_contact first (see agent.py force-injection), but
  // surfacing the lookup here makes whatsapp_send self-sufficient.
  if (!resolved) {
    const remote = await fetchUnifiedContacts(to, 3);
    const remoteJid = _pickJidFromUnified(remote);
    if (remoteJid) {
      resolved = { jid: remoteJid.jid, name: remoteJid.name, source: "unified-store" };
    }
  }
  if (!resolved) {
    return err(`Contact '${to}' not found. STOP and ASK the user for the correct name or full international phone number (with country code, e.g. +34XXXXXXXXX). Do NOT guess a number — silent delivery failures look identical to success.`);
  }
  if (resolved.ambiguous) {
    const candidateList = resolved.candidates
      .map((c) => `${c.name} (${extractPhone(c.jid) || c.jid})`)
      .join(", ");
    return err(`Contact '${to}' is AMBIGUOUS — matched ${resolved.candidates.length} contacts: ${candidateList}. ASK the user which one — do NOT pick blindly.`);
  }

  // Verify recipient is a real WhatsApp user when we routed via a bare phone number
  // OR when the cached contact is an unverified stub (no name AND no notify).
  // Closes the silent-drop bug: Baileys' sendMessage queues happily to non-existent JIDs.
  if (resolved.jid.endsWith("@s.whatsapp.net")) {
    const c = contacts.get(resolved.jid);
    const isStub = !c || ((!c.name || c.name === "") && (!c.notify || c.notify === ""));
    if (inputLooksLikePhone || isStub) {
      try {
        const [check] = await sock.onWhatsApp(resolved.jid);
        if (!check || !check.exists) {
          return err(`'${to}' (${resolved.jid}) is NOT a registered WhatsApp user. The number is likely missing a country code or wrong. STOP — ask the user for the correct full international number before retrying.`);
        }
      } catch (e) {
        log(`onWhatsApp check failed for ${resolved.jid} (continuing): ${e.message}`);
      }
    }
  }

  try {
    log(`Sending to ${resolved.name} (${resolved.jid}): "${message.slice(0, 50)}"`);
    // Presence subscription may fail on some JIDs — don't block send
    try {
      await sock.presenceSubscribe(resolved.jid);
      await sock.sendPresenceUpdate("composing", resolved.jid);
      await new Promise((r) => setTimeout(r, 800 + Math.random() * 1200));
      await sock.sendPresenceUpdate("paused", resolved.jid);
    } catch (presenceErr) {
      log(`Presence failed for ${resolved.jid} (continuing): ${presenceErr.message}`);
    }
    await sock.sendMessage(resolved.jid, { text: message });
    log(`Sent OK to ${resolved.name} (${resolved.jid})`);
    return ok({ sent: true, to: resolved.name, jid: resolved.jid });
  } catch (e) {
    log(`Send FAILED to ${resolved.name} (${resolved.jid}): ${e.message}\n${e.stack}`);
    return err(`Failed to send to ${resolved.name}: ${e.message}`);
  }
}

async function handleRead(args) {
  // FIX: Read tools work even when disconnected — return cached data.
  // Only send/presence operations need a live connection.
  const contact = args.contact || args.phone;
  const limit = args.limit || 10;

  // No contact specified — show recent chats overview + most recent chat messages
  if (!contact) {
    // Build summary of all chats sorted by most recent
    // Trigger lazy group name resolution for all groups with missing names
    for (const jid of messageStore.keys()) {
      if (jid.endsWith("@g.us") && !contacts.get(jid)?.name) {
        _resolveGroupName(jid);
      }
    }

    const chatSummary = [];
    for (const [jid, msgs] of messageStore) {
      if (jid === "status@broadcast") continue;
      const c = contacts.get(jid);
      const isGroupJid = jid.endsWith("@g.us");
      const name = c?.name || c?.notify || (isGroupJid ? `Group ${jid.split("@")[0].slice(-6)}` : null) || extractPhone(jid) || jid.split("@")[0];
      let latestTs = 0;
      let latestBody = "";
      let latestFrom = "";
      let incoming = 0;
      let contentCount = 0;
      for (const msg of msgs) {
        // Skip system stubs / reactions / protocol frames — they must not
        // become the chat's preview or inflate the counts (they render as
        // "[media]" junk otherwise).
        if (isNonContentMessage(msg)) continue;
        contentCount++;
        const ts = Number(msg.messageTimestamp || 0);
        if (!msg.key.fromMe) incoming++;
        if (ts > latestTs) {
          latestTs = ts;
          latestFrom = msg.key.fromMe ? "you" : (msg.pushName || name);
          latestBody =
            msg.message?.conversation ||
            msg.message?.extendedTextMessage?.text ||
            msg.message?.imageMessage?.caption ||
            (msg.message?.imageMessage ? "[photo]" : null) ||
            (msg.message?.videoMessage ? "[video]" : null) ||
            (msg.message?.audioMessage ? "[voice]" : null) ||
            (msg.message?.stickerMessage ? "[sticker]" : null) ||
            (msg.message?.documentMessage ? "[file]" : null) ||
            "[media]";
        }
      }
      // No content messages (e.g. a chat with only system events) — omit it.
      if (latestTs === 0) continue;
      chatSummary.push({
        name,
        type: jid.endsWith("@g.us") ? "group" : jid.endsWith("@lid") ? "lid" : "direct",
        muted: _isChatMuted(jid),
        last_from: latestFrom,
        last_message: (latestBody || "").slice(0, 80),
        last_time: new Date(latestTs * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC",
        total_messages: contentCount,
        incoming_messages: incoming,
        _ts: latestTs,
      });
    }
    chatSummary.sort((a, b) => b._ts - a._ts);

    // Remove internal sort field
    const overview = chatSummary.slice(0, limit).map(({ _ts, ...rest }) => rest);

    if (overview.length === 0) {
      return ok({ chats: [], messages: [], note: "No messages cached yet. Send or receive a message first." });
    }

    // Collect recent messages from ALL chats (not just the top one).
    // This gives the watcher cross-chat coverage for notifications.
    let allRecent = [];
    for (const [jid, msgs] of messageStore) {
      if (jid === "status@broadcast") continue;
      for (const msg of msgs) {
        // Keep the watcher mirror + cross-chat feed free of system stubs /
        // reactions / protocol frames that would show as "[media]".
        if (isNonContentMessage(msg)) continue;
        allRecent.push(msg);
      }
    }
    // Sort by timestamp descending, take the most recent ones
    allRecent.sort((a, b) => Number(b.messageTimestamp || 0) - Number(a.messageTimestamp || 0));
    const recentMessages = allRecent.slice(0, limit * 2).map(_formatMsg);

    return ok({
      chats: overview,
      most_recent_chat: overview[0]?.name || null,
      most_recent_messages: recentMessages,
      connected: isReady,
    });
  }

  const resolved = resolveContact(contact);
  if (!resolved) return err(`Contact '${contact}' not found. Use whatsapp_list_chats to see available contacts.`);

  // FIX: Use _getMessages which checks ALL JID variants (phone + lid)
  let stored = _getMessages(resolved.jid);

  // If still empty and connected, try on-demand fetch
  if (stored.length === 0 && isReady && sock) {
    log(`No cached messages for ${resolved.name} (${resolved.jid}) — requesting on-demand`);
    try {
      await sock.presenceSubscribe(resolved.jid);
      await sock.sendPresenceUpdate("available", resolved.jid);
      await sock.readMessages([{ remoteJid: resolved.jid, id: undefined, participant: undefined }]).catch(() => {});
      await new Promise((r) => setTimeout(r, 3000));
      stored = _getMessages(resolved.jid);
      if (stored.length > 0) {
        log(`On-demand fetch: got ${stored.length} messages for ${resolved.name}`);
      }
    } catch (e) {
      log(`On-demand message request failed: ${e.message}`);
    }
  }

  if (stored.length === 0) {
    return ok({
      contact: resolved.name,
      messages: [],
      connected: isReady,
      note: isReady
        ? "No messages cached yet. Messages appear after they are sent or received while connected."
        : "Disconnected and no cached messages. Call whatsapp_setup to reconnect.",
    });
  }

  // Drop system stubs / reactions / protocol frames, then sort by timestamp
  // descending and take most recent — the opened thread shows only real
  // messages, never "[media]" junk interleaved between them.
  const sorted = [...stored]
    .filter((m) => !isNonContentMessage(m))
    .sort((a, b) => Number(b.messageTimestamp || 0) - Number(a.messageTimestamp || 0));
  const results = sorted.slice(0, limit).map(_formatMsg);
  return ok({ contact: resolved.name, messages: results, connected: isReady });
}

async function handleListChats(args) {
  // FIX: Works even when disconnected — returns cached data.
  const limit = args.limit || 30;
  const includeGroups = args.include_groups !== false; // default true

  // FIX: Build chat list from messageStore (actual chats with messages),
  // not from contacts map (which has 780 entries, most without messages).
  const chatList = [];
  for (const [jid, msgs] of messageStore) {
    if (jid === "status@broadcast") continue;
    if (!includeGroups && jid.endsWith("@g.us")) continue;

    const c = contacts.get(jid);
    const isGroup = jid.endsWith("@g.us");
    const isLid = jid.endsWith("@lid");
    const displayName = c?.name || c?.notify || (isGroup ? `Group ${jid.split("@")[0].slice(-6)}` : null) || extractPhone(jid) || jid.split("@")[0];

    // Find most recent CONTENT message timestamp (skip system stubs /
    // reactions / protocol frames so the preview + count reflect real chat).
    let latestTs = 0;
    let latestPreview = "";
    let contentCount = 0;
    for (const msg of msgs) {
      if (isNonContentMessage(msg)) continue;
      contentCount++;
      const ts = Number(msg.messageTimestamp || 0);
      if (ts > latestTs) {
        latestTs = ts;
        const body =
          msg.message?.conversation ||
          msg.message?.extendedTextMessage?.text ||
          msg.message?.imageMessage?.caption ||
          (msg.message?.imageMessage ? "[image]" : null) ||
          (msg.message?.stickerMessage ? "[sticker]" : null) ||
          "[media]";
        latestPreview = (body || "").slice(0, 50);
      }
    }

    chatList.push({
      name: displayName,
      phone: extractPhone(jid),
      jid,
      type: isGroup ? "group" : isLid ? "lid" : "direct",
      muted: _isChatMuted(jid),
      messages_cached: contentCount,
      last_message_time: latestTs > 0 ? new Date(latestTs * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC" : null,
      last_message_preview: latestPreview,
    });
  }

  // Sort by most recent message first
  chatList.sort((a, b) => {
    const ta = a.last_message_time || "";
    const tb = b.last_message_time || "";
    return tb.localeCompare(ta);
  });

  if (chatList.length === 0) {
    return ok({ chats: [], connected: isReady, note: "No chats cached yet. Send or receive a message first." });
  }

  return ok({
    chats: chatList.slice(0, limit),
    total: chatList.length,
    connected: isReady,
  });
}

async function handleSearch(args) {
  // Search works even when disconnected for cached contacts.
  const { query } = args;
  if (!query) return err("query is required.");

  const q = query.toLowerCase().trim();
  const isPhoneQuery = /^\+?\d{7,}$/.test(q.replace(/[^0-9+]/g, ""));

  // Fire the unified-store lookup in parallel — these are the macOS-synced
  // contacts the brain saved in the phonebook. Without this, a saved number
  // returns "not found" until WhatsApp itself syncs the chat.
  const bridgePromise = fetchUnifiedContacts(query, 10);

  // ---- Phone-number path ----
  if (isPhoneQuery) {
    const out = [];
    if (isReady && sock) {
      try {
        const jid = formatJid(q);
        const [result] = await sock.onWhatsApp(jid);
        if (result && result.exists) {
          out.push({
            jid: result.jid,
            exists: true,
            phone: q,
            // Use the JID-variant-aware lookup so an @lid-stored name surfaces
            // even when onWhatsApp returns the @s.whatsapp.net JID.
            name: _displayNameForJid(result.jid) || _displayNameForJid(jid) || "",
            source: "onWhatsApp",
          });
        } else {
          out.push({ phone: q, exists: false, source: "onWhatsApp" });
        }
      } catch (e) {
        log(`whatsapp_search onWhatsApp failed: ${e.message}`);
      }
    } else {
      // Offline — search cached contacts by phone
      for (const [jid, c] of contacts) {
        const phoneDigits = (c.phone || "").replace(/[^0-9]/g, "");
        const qDigits = q.replace(/[^0-9]/g, "");
        if (phoneDigits && qDigits && phoneDigits.includes(qDigits)) {
          out.push({ name: c.name || c.notify || "", phone: c.phone, jid, source: "cache" });
        }
      }
    }
    const fromBridge = await bridgePromise;
    return ok(_mergeContactMatches(out, fromBridge, q));
  }

  // ---- Name path ----
  const matches = [];
  for (const [jid, c] of contacts) {
    const name = (c.name || "").toLowerCase();
    const notify = (c.notify || "").toLowerCase();
    const phoneDigits = (c.phone || "").replace(/[^0-9]/g, "");
    if (name.includes(q) || notify.includes(q) || (phoneDigits && q.replace(/[^0-9]/g, "") && phoneDigits.includes(q.replace(/[^0-9]/g, "")))) {
      matches.push({ name: c.name || c.notify || "", phone: c.phone, jid, source: "cache" });
    }
    if (matches.length >= 20) break;
  }

  const fromBridge = await bridgePromise;
  const merged = _mergeContactMatches(matches, fromBridge, q);

  if (merged.length === 0) {
    return ok({
      matches: [],
      note: `No contacts matching '${query}'. Contacts sync over time as messages arrive. If the person is saved in macOS Contacts, run sync_macos_contacts first.`,
    });
  }
  return ok(merged.slice(0, 10));
}

/**
 * Merge cached WhatsApp matches with unified-store matches.
 * Dedup by phone digits (most reliable cross-cache identity).
 * Unified-store rows surface as additional rows with source="unified-store".
 */
function _mergeContactMatches(cacheRows, unifiedRows, query) {
  const out = [];
  const seenPhones = new Set();
  const seenJids = new Set();

  const phoneOf = (row) => {
    if (row.phone) return row.phone.replace(/[^0-9]/g, "");
    if (row.jid && row.jid.endsWith("@s.whatsapp.net")) return row.jid.split("@")[0].split(":")[0];
    return "";
  };

  for (const r of cacheRows) {
    const p = phoneOf(r);
    if (p) seenPhones.add(p);
    if (r.jid) seenJids.add(normalizeJid(r.jid));
    out.push(r);
  }

  for (const u of (unifiedRows || [])) {
    const handles = u.handles || {};
    const phones = handles.phone || [];
    const phoneList = Array.isArray(phones) ? phones : [phones];
    const firstPhone = phoneList.find((p) => p);
    const phoneDigits = (firstPhone || "").replace(/[^0-9]/g, "");
    if (phoneDigits && seenPhones.has(phoneDigits)) continue;

    const jid = handles.whatsapp_jid || (phoneDigits ? `${phoneDigits}@s.whatsapp.net` : "");
    if (jid && seenJids.has(normalizeJid(jid))) continue;

    out.push({
      name: u.name,
      phone: firstPhone || "",
      jid,
      aliases: u.aliases || [],
      handles: u.handles || {},
      source: "unified-store",
    });
    if (phoneDigits) seenPhones.add(phoneDigits);
    if (jid) seenJids.add(normalizeJid(jid));
  }
  return out;
}

/** Resolve a send target by name/phone (local cache → unified store).
 *  Returns { resolved } on success or { error: <mcp err result> } on failure. */
async function _resolveSendTarget(to) {
  let resolved = resolveContact(to);
  if (!resolved) {
    const remote = await fetchUnifiedContacts(to, 3);
    const remoteJid = _pickJidFromUnified(remote);
    if (remoteJid) resolved = { jid: remoteJid.jid, name: remoteJid.name, source: "unified-store" };
  }
  if (!resolved) return { error: err(`Contact '${to}' not found.`) };
  if (resolved.ambiguous) {
    const candidateList = resolved.candidates.map((c) => `${c.name} (${extractPhone(c.jid) || c.jid})`).join(", ");
    return { error: err(`Contact '${to}' is AMBIGUOUS — matched ${resolved.candidates.length}: ${candidateList}. Ask the user which one.`) };
  }
  return { resolved };
}

/** Shared guts of all media send tools: validate → resolve → read → send. */
async function _sendMediaFile(kind, to, filePath, opts) {
  if (!isReady || !sock) return err("WhatsApp not connected. Call whatsapp_setup first.");
  if (!to || !filePath) return err(`Both 'to' and the ${kind} file path are required.`);
  if (!fs.existsSync(filePath)) return err(`File not found: ${filePath}`);

  const { resolved, error } = await _resolveSendTarget(to);
  if (error) return error;

  try {
    const buffer = fs.readFileSync(filePath);
    const payload = buildSendPayload(kind, buffer, {
      mimetype: mimeForPath(filePath),
      ...opts,
    });
    await sock.sendMessage(resolved.jid, payload);
    return ok({ sent: true, kind, to: resolved.name, file: filePath });
  } catch (e) {
    return err(`Failed to send ${kind} to ${resolved.name}: ${e.message}`);
  }
}

async function handleSendImage(args) {
  return _sendMediaFile("image", args.to || args.phone, args.image_path, {
    caption: args.caption,
  });
}

async function handleSendAudio(args) {
  return _sendMediaFile("audio", args.to || args.phone, args.audio_path, {
    voiceNote: args.voice_note !== false,
  });
}

async function handleSendVideo(args) {
  return _sendMediaFile("video", args.to || args.phone, args.video_path, {
    caption: args.caption,
  });
}

async function handleSendDocument(args) {
  const filePath = args.file_path;
  return _sendMediaFile("document", args.to || args.phone, filePath, {
    caption: args.caption,
    fileName: args.file_name || (filePath ? path.basename(filePath) : "file"),
  });
}

/** Locate a raw stored message by id, optionally narrowed to one chat. */
function _findStoredMessage(messageId, jidHint) {
  if (jidHint) {
    const msgs = _getMessages(jidHint);
    const hit = msgs.find((m) => m.key && m.key.id === messageId);
    if (hit) return hit;
  }
  for (const [jid, msgs] of messageStore) {
    if (jid === "status@broadcast") continue;
    const hit = msgs.find((m) => m.key && m.key.id === messageId);
    if (hit) return hit;
  }
  return null;
}

async function handleDownloadMedia(args) {
  const messageId = args.message_id;
  if (!messageId) return err("'message_id' is required — use the 'id' from whatsapp_read results.");

  let jidHint = null;
  if (args.contact) {
    const r = resolveContact(args.contact);
    if (r && !r.ambiguous) jidHint = r.jid;
  }

  const msg = _findStoredMessage(messageId, jidHint);
  if (!msg) {
    return err(
      `Message '${messageId}' not found in the local cache. ` +
      "Run whatsapp_read for that chat first, then retry with the fresh id.",
    );
  }

  const media = describeMedia(msg);
  if (!media) return err(`Message '${messageId}' carries no media (text-only).`);

  try {
    const { downloadMediaMessage } = require("@whiskeysockets/baileys");
    const silentLogger = { trace() {}, debug() {}, info() {}, warn() {}, error() {}, fatal() {}, child() { return this; }, level: "silent" };
    const buffer = await downloadMediaMessage(
      msg,
      "buffer",
      {},
      sock
        ? { logger: silentLogger, reuploadRequest: sock.updateMediaMessage }
        : undefined,
    );
    if (!buffer || !buffer.length) return err("Download produced no data (media may have expired on WhatsApp servers).");

    // Prefer the document's own filename; fall back to <id>.<ext-from-mime>.
    const safeName = media.file_name
      ? media.file_name.replace(/[^\w.\- ]+/g, "_")
      : `${messageId}.${extForMime(media.mimetype)}`;
    const outPath = path.join(MEDIA_DIR, `${messageId}_${safeName}`.slice(0, 200));
    fs.writeFileSync(outPath, buffer);

    return ok({
      path: outPath,
      kind: media.kind,
      mimetype: media.mimetype,
      voice_note: media.voice_note,
      seconds: media.seconds,
      file_name: media.file_name,
      size_bytes: buffer.length,
    });
  } catch (e) {
    return err(`Media download failed: ${e.message}`);
  }
}

async function handleMute(args) {
  const chatQuery = args.chat;
  const action = args.action || "mute";
  if (!chatQuery) return err("'chat' is required — name or phone of the chat to mute.");

  // Resolve the chat/group — for mute, prefer group matches over person matches
  // because "mute Salah Surf Casting" almost certainly means the group, not a person
  const q = chatQuery.toLowerCase().trim();

  // First pass: look for exact group name match (@g.us)
  let resolved = null;
  for (const [jid, c] of contacts) {
    if (!jid.endsWith("@g.us")) continue;
    const name = (c.name || "").toLowerCase();
    if (name === q) {
      resolved = { jid, name: c.name || jid };
      break;
    }
  }

  // Second pass: partial group name match
  if (!resolved) {
    for (const [jid, c] of contacts) {
      if (!jid.endsWith("@g.us")) continue;
      const name = (c.name || "").toLowerCase();
      if (name.includes(q) || q.includes(name)) {
        resolved = { jid, name: c.name || jid };
        break;
      }
    }
  }

  // Third pass: fall back to resolveContact (persons + all contacts).
  // For mute we don't want ambiguous-error UX (mute should be tolerant), so
  // drop ambiguous shapes here — the broad partial-search fallback below
  // will still pick the first textual hit, which is correct for mute intent.
  if (!resolved) {
    const r = resolveContact(chatQuery);
    if (r && !r.ambiguous) resolved = r;
  }

  // Last resort: broad partial search
  if (!resolved) {
    for (const [jid, c] of contacts) {
      const name = (c.name || "").toLowerCase();
      if (name.includes(q)) {
        resolved = { jid, name: c.name || jid };
        break;
      }
    }
    if (!resolved) return err(`Chat '${chatQuery}' not found. Use whatsapp_list_chats to see available chats.`);
  }

  if (action === "unmute") {
    mutedChats.delete(resolved.jid);
    saveMuted();
    log(`Manual unmute: ${resolved.name} (${resolved.jid})`);
    // Also unmute on WhatsApp if connected
    if (isReady && sock) {
      try {
        await sock.chatModify({ mute: null }, resolved.jid);
        log(`WhatsApp unmute synced: ${resolved.name}`);
        return ok({ action: "unmuted", chat: resolved.name, synced: true });
      } catch (e) {
        log(`WhatsApp unmute sync failed: ${e.message}`);
        return ok({ action: "unmuted", chat: resolved.name, synced: false, note: "Unmuted locally. WhatsApp sync failed." });
      }
    }
    return ok({ action: "unmuted", chat: resolved.name, synced: false, note: "Unmuted locally. Not connected to sync to WhatsApp." });
  }

  // Mute
  mutedChats.set(resolved.jid, -1); // -1 = muted forever
  saveMuted();
  log(`Manual mute: ${resolved.name} (${resolved.jid})`);
  // Also mute on WhatsApp if connected
  if (isReady && sock) {
    try {
      // Mute for 1 year (effectively forever) — WhatsApp doesn't accept -1 via chatModify
      await sock.chatModify({ mute: Date.now() + 365 * 24 * 60 * 60 * 1000 }, resolved.jid);
      log(`WhatsApp mute synced: ${resolved.name}`);
      return ok({ action: "muted", chat: resolved.name, synced: true });
    } catch (e) {
      log(`WhatsApp mute sync failed: ${e.message}`);
      return ok({ action: "muted", chat: resolved.name, synced: false, note: "Muted locally. WhatsApp sync failed — you may still see it in the app." });
    }
  }
  return ok({ action: "muted", chat: resolved.name, synced: false, note: "Muted locally. Not connected to sync to WhatsApp." });
}

async function handleListMuted() {
  const result = [];
  for (const [jid, val] of mutedChats) {
    const c = contacts.get(jid);
    const isGroup = jid.endsWith("@g.us");
    const name = c?.name || (isGroup ? `Group ${jid.split("@")[0].slice(-6)}` : null) || extractPhone(jid) || jid.split("@")[0];
    result.push({
      name,
      jid,
      type: isGroup ? "group" : "direct",
      muted_until: val === -1 ? "forever" : new Date(val * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC",
      currently_muted: _isChatMuted(jid),
    });
  }
  // Sort: groups first, then by name
  result.sort((a, b) => {
    if (a.type !== b.type) return a.type === "group" ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
  return ok({ muted_chats: result, total: result.length });
}

// ---------------------------------------------------------------------------
// MCP server
// ---------------------------------------------------------------------------

const mcpServer = new Server(
  { name: "mcp-whatsapp", version: "0.3.0" },
  { capabilities: { tools: {} } }
);

mcpServer.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

mcpServer.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  switch (name) {
    case "whatsapp_setup": return handleSetup();
    case "whatsapp_status": return handleStatus();
    case "whatsapp_send": return handleSend(args);
    case "whatsapp_read": return handleRead(args);
    case "whatsapp_list_chats": return handleListChats(args);
    case "whatsapp_search": return handleSearch(args);
    case "whatsapp_send_image": return handleSendImage(args);
    case "whatsapp_send_audio": return handleSendAudio(args);
    case "whatsapp_send_video": return handleSendVideo(args);
    case "whatsapp_send_document": return handleSendDocument(args);
    case "whatsapp_download_media": return handleDownloadMedia(args);
    case "whatsapp_mute": return handleMute(args);
    case "whatsapp_list_muted": return handleListMuted();
    default: return err(`Unknown tool: ${name}`);
  }
});

// ---------------------------------------------------------------------------
// Graceful shutdown
// ---------------------------------------------------------------------------

async function shutdown() {
  log("Shutting down (preserving session)...");
  try { if (sock) sock.end(undefined); } catch (_) {}
  process.exit(0);
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  // MCP transport FIRST
  const transport = new StdioServerTransport();
  await mcpServer.connect(transport);
  log("mcp-whatsapp v0.3.0 (Baileys) running on stdio");
  loadContacts();
  loadMessages();
  loadMuted();

  // Start WhatsApp (no browser!)
  await startWhatsApp();
}

main().catch((e) => {
  log(`Fatal: ${e.message}`);
  process.exit(1);
});
