#!/usr/bin/env node

const path = require("path");
const fs = require("fs");
const https = require("https");
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

const LOG_PATH = path.join(DATA_DIR, "whatsapp-mcp.log");
const log = (msg) => {
  const line = `[${new Date().toISOString()}] ${msg}\n`;
  process.stderr.write(line);
  try { fs.appendFileSync(LOG_PATH, line); } catch (_) {}
};

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

function acquireLock() {
  try {
    // Check if another process holds the lock
    if (fs.existsSync(LOCK_FILE)) {
      const lockData = JSON.parse(fs.readFileSync(LOCK_FILE, "utf8"));
      const age = Date.now() - lockData.time;
      // Lock is stale if older than 30s (process probably crashed)
      if (age < 30000) {
        const otherPid = lockData.pid;
        // Check if process is still alive
        try {
          process.kill(otherPid, 0);
          // Process alive — check if it's orphaned (ppid=1)
          try {
            const { execSync } = require("child_process");
            const ppid = execSync(`ps -o ppid= -p ${otherPid}`, { timeout: 2000 }).toString().trim();
            if (ppid === "1") {
              // Orphaned zombie — kill it and take over
              log(`Killing orphaned WhatsApp process (pid=${otherPid}, ppid=1)`);
              try { process.kill(otherPid, "SIGTERM"); } catch (_) {}
            } else {
              return false; // Legitimate process holds the lock
            }
          } catch (_) {
            return false; // Can't check ppid — assume legitimate
          }
        } catch (_) { /* dead — take over */ }
      }
    }
    fs.writeFileSync(LOCK_FILE, JSON.stringify({ pid: process.pid, time: Date.now() }));
    return true;
  } catch (_) { return true; }
}

function releaseLock() {
  try { fs.unlinkSync(LOCK_FILE); } catch (_) {}
}

// Refresh lock periodically
setInterval(() => {
  try { fs.writeFileSync(LOCK_FILE, JSON.stringify({ pid: process.pid, time: Date.now() })); } catch (_) {}
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
    fs.writeFileSync(LOCK_FILE, JSON.stringify({ pid: process.pid, time: Date.now() }));
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
      setTimeout(() => {
        const totalMsgs = [...messageStore.values()].reduce((sum, msgs) => sum + msgs.length, 0);
        const lidCount = [...contacts.keys()].filter((k) => k.endsWith("@lid")).length;
        const phoneCount = [...contacts.keys()].filter((k) => k.endsWith("@s.whatsapp.net")).length;
        log(`After connect: ${contacts.size} contacts (${phoneCount} phone, ${lidCount} LID), ${totalMsgs} messages cached`);
        _buildLidMap();

        // Resolve missing group names in background
        let groupsToResolve = 0;
        for (const jid of contacts.keys()) {
          if (jid.endsWith("@g.us") && !contacts.get(jid)?.name) {
            _resolveGroupName(jid);
            groupsToResolve++;
          }
        }
        if (groupsToResolve > 0) {
          log(`Resolving ${groupsToResolve} group names in background...`);
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
  sock.ev.on("messaging-history.set", (event) => {
    const before = contacts.size;
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
      }
      // Track mute — Baileys protobuf field is "muteEndTime" (uint64)
      // Normalized: -1 = muted forever, >0 = muted until epoch seconds, 0 = not muted
      const muteVal = toMuteNumber(chat.muteEndTime);
      if (muteVal !== 0) {
        mutedChats.set(chat.id, muteVal);
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
      }
      const chatMuteVal = toMuteNumber(chat.muteEndTime);
      if (chatMuteVal !== 0) {
        mutedChats.set(jid, chatMuteVal);
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
      // Update mute status — use "in" check to catch both null (unmute) and values
      // Baileys sends muteEndTime: null on unmute, muteEndTime: <number|Long> on mute
      if ("muteEndTime" in u) {
        const muteVal = toMuteNumber(u.muteEndTime);
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
    for (const msg of (event.messages || [])) {
      const jid = msg.key?.remoteJid;
      if (!jid || jid === "status@broadcast") continue;
      if (!messageStore.has(jid)) messageStore.set(jid, []);
      const chatMsgs = messageStore.get(jid);
      const msgId = msg.key.id;
      if (!chatMsgs.some((m) => m.key.id === msgId)) {
        chatMsgs.push(msg);
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
  });
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

/** Check if a chat is currently muted. */
function _isChatMuted(jid) {
  const muteExpiry = mutedChats.get(jid);
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
  // "34664476256:18@s.whatsapp.net" → "34664476256@s.whatsapp.net"
  // Group JIDs (@g.us) are left untouched
  if (jid.includes("@s.whatsapp.net")) {
    const phone = jid.split("@")[0].split(":")[0];
    return phone + "@s.whatsapp.net";
  }
  return jid;
}

/** Resolve name or phone to JID. Returns { jid, name } or null. */
function resolveContact(query) {
  const q = query.toLowerCase().trim();

  // Direct phone number — always use @s.whatsapp.net format
  if (/^\+?\d{7,}$/.test(q.replace(/[^0-9+]/g, ""))) {
    return { jid: formatJid(q), name: q };
  }

  // Search by name, notify, or phone in contacts
  // Prefer @s.whatsapp.net JIDs over @lid for sending (more reliable)
  let best = null;
  let bestScore = 0;
  for (const [jid, c] of contacts) {
    const name = (c.name || "").toLowerCase();
    const notify = (c.notify || "").toLowerCase();
    const phone = (c.phone || "").toLowerCase();
    const isPhoneJid = jid.endsWith("@s.whatsapp.net");

    // Exact match on name or notify
    if (name === q || notify === q) {
      const result = { jid: normalizeJid(jid), name: c.name || c.notify };
      if (isPhoneJid) return result; // Best possible — phone JID + exact name
      // Keep @lid match but keep looking for phone JID equivalent
      if (!best || bestScore < 999 || !best.jid.endsWith("@s.whatsapp.net")) {
        best = result;
        bestScore = 999;
      }
      continue;
    }
    // Exact match on phone
    if (phone && phone === q) return { jid: normalizeJid(jid), name: c.name || c.notify || c.phone || "Unknown" };
    // Partial match scoring — bonus for phone JIDs
    const score = (name.includes(q) ? 2 : 0)
      + (notify.includes(q) ? 2 : 0)
      + (phone && phone.includes(q) ? 1 : 0)
      + (isPhoneJid ? 1 : 0);
    if (score > bestScore) {
      best = { jid: normalizeJid(jid), name: c.name || c.notify || c.phone || "Unknown" };
      bestScore = score;
    }
  }
  return best;
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
  let chatName = contacts.get(jid)?.name
    || (isGroup ? "Group" : null)  // Don't show JID fragments for groups
    || extractPhone(jid)
    || jid.split("@")[0];

  // Trigger lazy group name fetch if missing
  if (isGroup && !contacts.get(jid)?.name) {
    _resolveGroupName(jid);
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
    muted: isMuted,
  };

  // For group messages: "from" = person who sent, add group context
  if (isGroup && !msg.key.fromMe) {
    const partJid = msg.key.participant || "";
    // Resolve participant name: pushName → contacts → LID→phone contacts → cleaned JID
    const partContact = contacts.get(partJid);
    const partName = msg.pushName
      || partContact?.name || partContact?.notify
      || (lidToPhone.has(partJid) ? contacts.get(lidToPhone.get(partJid))?.name : "")
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
        to: { type: "string", description: 'Contact name or phone number. E.g. "lisichka" or "34612345678"' },
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
        to: { type: "string", description: 'Contact name or phone number. E.g. "lisichka" or "34612345678"' },
        image_path: { type: "string", description: "Absolute path to the image file" },
        caption: { type: "string", description: "Optional caption for the image" },
      },
      required: ["to", "image_path"],
    },
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

  const resolved = resolveContact(to);
  if (!resolved) return err(`Contact '${to}' not found. Try a phone number or use whatsapp_list_chats to see available contacts.`);

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
      const name = c?.name || c?.notify || (isGroupJid ? "Group" : null) || extractPhone(jid) || jid.split("@")[0];
      let latestTs = 0;
      let latestBody = "";
      let latestFrom = "";
      let incoming = 0;
      for (const msg of msgs) {
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
      if (latestTs === 0) continue;
      chatSummary.push({
        name,
        type: jid.endsWith("@g.us") ? "group" : jid.endsWith("@lid") ? "lid" : "direct",
        muted: _isChatMuted(jid),
        last_from: latestFrom,
        last_message: (latestBody || "").slice(0, 80),
        last_time: new Date(latestTs * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC",
        total_messages: msgs.length,
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

  // Sort by timestamp descending, take most recent
  const sorted = [...stored].sort((a, b) => Number(b.messageTimestamp || 0) - Number(a.messageTimestamp || 0));
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
    const displayName = c?.name || c?.notify || (isGroup ? "Group" : null) || extractPhone(jid) || jid.split("@")[0];

    // Find most recent message timestamp
    let latestTs = 0;
    let latestPreview = "";
    for (const msg of msgs) {
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
      messages_cached: msgs.length,
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
  // FIX: Search works even when disconnected for cached contacts.
  const { query } = args;
  if (!query) return err("query is required.");

  const q = query.toLowerCase().trim();

  // Check if query looks like a phone number — needs live connection for onWhatsApp()
  if (/^\+?\d{7,}$/.test(q.replace(/[^0-9+]/g, ""))) {
    if (isReady && sock) {
      try {
        const jid = formatJid(q);
        const [result] = await sock.onWhatsApp(jid);
        if (result && result.exists) {
          const c = contacts.get(result.jid);
          return ok([{ jid: result.jid, exists: true, phone: q, name: c?.name || "" }]);
        }
        return ok([{ phone: q, exists: false }]);
      } catch (e) {
        return err(`Failed to search: ${e.message}`);
      }
    }
    // Offline — search cached contacts by phone
    const matches = [];
    for (const [jid, c] of contacts) {
      if (c.phone && c.phone.includes(q)) {
        matches.push({ name: c.name || c.notify, phone: c.phone, jid });
      }
    }
    if (matches.length > 0) return ok(matches);
    return ok({ message: `Can't verify phone online (disconnected). No cached match for '${q}'.` });
  }

  // Name search in synced contacts
  const matches = [];
  for (const [jid, c] of contacts) {
    const name = (c.name || "").toLowerCase();
    const notify = (c.notify || "").toLowerCase();
    if (name.includes(q) || notify.includes(q) || (c.phone && c.phone.includes(q))) {
      matches.push({ name: c.name || c.notify, phone: c.phone, jid });
    }
    if (matches.length >= 10) break;
  }

  if (matches.length === 0) {
    return ok({ message: `No contacts matching '${query}'. Contacts sync over time as messages arrive. Try a phone number.` });
  }
  return ok(matches);
}

async function handleSendImage(args) {
  if (!isReady || !sock) return err("WhatsApp not connected. Call whatsapp_setup first.");
  const to = args.to || args.phone;
  const { image_path, caption } = args;
  if (!to || !image_path) return err("Both 'to' and 'image_path' are required.");
  if (!fs.existsSync(image_path)) return err(`File not found: ${image_path}`);

  const resolved = resolveContact(to);
  if (!resolved) return err(`Contact '${to}' not found.`);

  try {
    const imageBuffer = fs.readFileSync(image_path);
    await sock.sendMessage(resolved.jid, {
      image: imageBuffer,
      caption: caption || undefined,
    });
    return ok({ sent: true, to: resolved.name, image: image_path });
  } catch (e) {
    return err(`Failed to send image to ${resolved.name}: ${e.message}`);
  }
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
