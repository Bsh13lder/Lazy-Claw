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
const MAX_MESSAGES_PER_CHAT = 100;

const CONTACTS_FILE = path.join(DATA_DIR, "contacts.json");
const MESSAGES_FILE = path.join(DATA_DIR, "messages.json");

// Debounce message saves — avoid disk thrashing on burst of messages
let _msgSaveTimer = null;

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
      // Lock is stale if older than 60s (process probably crashed)
      if (age < 60000) {
        const otherPid = lockData.pid;
        // Check if process is still alive
        try { process.kill(otherPid, 0); return false; } catch (_) { /* dead */ }
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

async function startWhatsApp() {
  // Prevent 440 loop: only one process should connect at a time
  // Skip lock for explicit setup calls (user wants to reconnect)
  if (!acquireLock()) {
    log("Another WhatsApp MCP process holds the lock — taking over");
    releaseLock();
    // Small delay to let the other process notice
    await new Promise((r) => setTimeout(r, 500));
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
      keys: makeCacheableSignalKeyStore(state.keys, { trace() {}, debug() {}, info() {}, warn() {}, error() {}, fatal() {}, child() { return this; }, level: 'silent' }),
    },
    printQRInTerminal: false,
    browser: ["LazyClaw", "Desktop", "1.0.0"],
    generateHighQualityLinkPreview: false,
    syncFullHistory: true,
    shouldSyncHistoryMessage: () => true,
    logger: { trace() {}, debug() {}, info() {}, warn() {}, error() {}, fatal() {}, child() { return this; }, level: 'silent' },
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
    // Also extract contacts from chats
    for (const chat of (event.chats || [])) {
      if (!contacts.has(chat.id)) {
        contacts.set(chat.id, {
          name: chat.name || extractPhone(chat.id) || chat.id.split("@")[0],
          notify: "",
          phone: extractPhone(chat.id),
        });
      }
    }
    const added = contacts.size - before;
    if (added > 0) {
      saveContacts();
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
        contacts.set(jid, {
          name: chat.name || extractPhone(jid) || jid.split("@")[0],
          phone: extractPhone(jid),
        });
      }
    }
  });

  // Track message senders + store messages for reading
  sock.ev.on("messages.upsert", ({ messages: msgs, type }) => {
    if (msgs.length > 0) {
      log(`messages.upsert: ${msgs.length} messages (type=${type})`);
    }
    for (const msg of msgs) {
      const jid = msg.key.remoteJid;
      if (!jid || jid === "status@broadcast") continue;

      // Update contact info
      if (!contacts.has(jid)) {
        contacts.set(jid, {
          name: msg.pushName || extractPhone(jid) || jid.split("@")[0],
          notify: msg.pushName || "",
          phone: extractPhone(jid),
        });
      } else if (msg.pushName) {
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
    description: "Read recent WhatsApp messages. Without contact: reads the most recent chat. With contact: reads that specific chat. Call this FIRST for any WhatsApp task.",
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
    description: "List recent WhatsApp chats.",
    inputSchema: {
      type: "object",
      properties: {
        limit: { type: "number", description: "Number of chats to return (default 20)" },
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
  // Not connected — start/restart WhatsApp
  if (!sock) {
    log("Setup called — starting WhatsApp connection...");
    await startWhatsApp();
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
  if (isReady && sock) {
    const me = sock.user;
    return ok({ status: "connected", phone: me ? me.id.split(":")[0] : "unknown" });
  }
  if (latestQR) return ok({ status: "qr_needed" });
  return ok({ status: "disconnected" });
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
  if (!isReady || !sock) return err("WhatsApp not connected. Call whatsapp_setup first.");
  const contact = args.contact || args.phone;
  const limit = args.limit || 10;

  // No contact specified — read the most recent chat
  if (!contact) {
    // Find the chat with the most recent message
    let latestJid = null;
    let latestTs = 0;
    for (const [jid, msgs] of messageStore) {
      if (jid === "status@broadcast") continue;
      for (const msg of msgs) {
        const ts = Number(msg.messageTimestamp || 0);
        if (ts > latestTs) { latestTs = ts; latestJid = jid; }
      }
    }
    if (!latestJid) {
      return ok({ messages: [], note: "No messages cached yet." });
    }
    const c = contacts.get(latestJid);
    const contactName = c?.name || c?.notify || extractPhone(latestJid) || latestJid.split("@")[0];
    // Rewrite args with resolved contact and recurse
    return handleRead({ contact: contactName, limit });
  }

  const resolved = resolveContact(contact);
  if (!resolved) return err(`Contact '${contact}' not found. Use whatsapp_list_chats to see available contacts.`);

  // Read from in-memory message store (populated via messages.upsert + history sync)
  const stored = messageStore.get(resolved.jid) || [];

  if (stored.length === 0) {
    // Check if any other JID variant has messages (e.g. @lid vs @s.whatsapp.net for same contact)
    const phone = resolved.jid.split("@")[0].split(":")[0];
    for (const [jid, msgs] of messageStore) {
      if (jid.split("@")[0].split(":")[0] === phone && msgs.length > 0) {
        stored.push(...msgs);
        break;
      }
    }
  }

  if (stored.length === 0) {
    // On-demand: trigger presence + mark-read to nudge WhatsApp into delivering messages
    log(`No cached messages for ${resolved.name} (${resolved.jid}) — requesting on-demand`);
    try {
      await sock.presenceSubscribe(resolved.jid);
      await sock.sendPresenceUpdate("available", resolved.jid);
      // Send read receipt — triggers message delivery for unread chats
      await sock.readMessages([{ remoteJid: resolved.jid, id: undefined, participant: undefined }]).catch(() => {});
      // Wait for messages.upsert events to fire
      await new Promise((r) => setTimeout(r, 3000));
      // Re-check cache after waiting
      const fresh = messageStore.get(resolved.jid) || [];
      if (fresh.length > 0) {
        stored.push(...fresh);
        log(`On-demand fetch: got ${fresh.length} messages for ${resolved.name}`);
      } else {
        // Try JID variant lookup again
        const phone = resolved.jid.split("@")[0].split(":")[0];
        for (const [jid, msgs] of messageStore) {
          if (jid.split("@")[0].split(":")[0] === phone && msgs.length > 0) {
            stored.push(...msgs);
            log(`On-demand fetch: found ${msgs.length} messages via JID variant ${jid}`);
            break;
          }
        }
      }
    } catch (e) {
      log(`On-demand message request failed: ${e.message}`);
    }
  }

  if (stored.length === 0) {
    return ok({
      contact: resolved.name,
      messages: [],
      note: "No messages cached yet. Messages appear after they are sent or received while connected. Try sending a message to this contact first.",
    });
  }

  // Sort by timestamp descending, take most recent
  const sorted = [...stored].sort((a, b) => {
    const ta = Number(a.messageTimestamp || 0);
    const tb = Number(b.messageTimestamp || 0);
    return tb - ta;
  });

  const results = sorted.slice(0, limit).map((msg) => {
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
    // Convert Unix timestamp to readable format
    const ts = Number(msg.messageTimestamp || 0);
    const time = ts > 0 ? new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC" : "unknown";
    return {
      from: msg.key.fromMe ? "me" : (contacts.get(msg.key.remoteJid)?.name || msg.pushName || msg.key.remoteJid),
      body,
      time,
      fromMe: msg.key.fromMe,
    };
  });

  return ok({ contact: resolved.name, messages: results });
}

async function handleListChats(args) {
  if (!isReady || !sock) return err("WhatsApp not connected. Call whatsapp_setup first.");
  const limit = args.limit || 20;

  const results = [];
  for (const [jid, c] of contacts) {
    // Include direct chats: @s.whatsapp.net (phone) and @lid (Linked Identity, Baileys v6)
    // Skip groups (@g.us) and broadcast
    if (jid.endsWith("@s.whatsapp.net") || jid.endsWith("@lid")) {
      const phone = extractPhone(jid);
      const displayName = c.name || c.notify || phone || "Unknown";
      const msgCount = (messageStore.get(jid) || []).length;
      results.push({
        name: displayName,
        phone,
        jid,
        messages_cached: msgCount,
      });
    }
    if (results.length >= limit) break;
  }

  if (results.length === 0) {
    return ok({ message: "No contacts synced yet. Send or receive a message first, contacts build up over time." });
  }
  return ok(results);
}

async function handleSearch(args) {
  if (!isReady || !sock) return err("WhatsApp not connected. Call whatsapp_setup first.");
  const { query } = args;
  if (!query) return err("query is required.");

  const q = query.toLowerCase().trim();

  // Check if query looks like a phone number
  if (/^\+?\d{7,}$/.test(q.replace(/[^0-9+]/g, ""))) {
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
  { name: "mcp-whatsapp", version: "0.2.0" },
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
  log("mcp-whatsapp v0.2.0 (Baileys) running on stdio");
  loadContacts();
  loadMessages();

  // Start WhatsApp (no browser!)
  await startWhatsApp();
}

main().catch((e) => {
  log(`Fatal: ${e.message}`);
  process.exit(1);
});
