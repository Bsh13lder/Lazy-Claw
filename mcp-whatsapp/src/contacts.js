"use strict";

// Contact identity for WhatsApp: name precedence, LID↔phone mapping, and
// non-destructive merging of the partial contact records Baileys emits.
//
// WHY THIS MODULE EXISTS
//
// WhatsApp describes a contact through several independent sources of varying
// quality, and Baileys delivers them in separate, partial events:
//
//   contactAction.fullName  the name YOU saved  (best — arrives only via the
//                           `critical_unblock_low` app-state collection)
//   verifiedName            a business's verified name
//   pushName / notify       the name THEY chose for themselves
//   phone number            derived from an @s.whatsapp.net JID
//
// Collapsing these into one `name` field loses the provenance, so a pushName
// can never be upgraded once the real saved name shows up — and a partial
// event can silently destroy a good name. Each source therefore gets its own
// slot here, merging only ever upgrades a field, and precedence is applied at
// read time by displayName().
//
// All functions are pure and return new objects — never mutate a record.

const PHONE_SUFFIX = "@s.whatsapp.net";
const LID_SUFFIX = "@lid";
const GROUP_SUFFIX = "@g.us";

/** "34604246401:18@s.whatsapp.net" → "+34604246401". null for LID/group/blank. */
function extractPhone(jid) {
  if (!jid || !jid.endsWith(PHONE_SUFFIX)) return null;
  const num = jid.split("@")[0].split(":")[0];
  return num ? "+" + num : null;
}

/** "+34604246401" → "34604246401@s.whatsapp.net". null if not a phone. */
function phoneToJid(phone) {
  if (!phone) return null;
  const digits = String(phone).replace(/[^0-9]/g, "");
  return digits ? digits + PHONE_SUFFIX : null;
}

/** Strip the multi-device suffix from phone JIDs. Group/LID JIDs pass through. */
function normalizeJid(jid) {
  if (!jid) return jid;
  if (jid.includes(PHONE_SUFFIX)) {
    const phone = jid.split("@")[0].split(":")[0];
    return phone + PHONE_SUFFIX;
  }
  return jid;
}

function isGroupJid(jid) {
  return typeof jid === "string" && jid.endsWith(GROUP_SUFFIX);
}

/** A blank contact record — the canonical shape every caller should start from. */
function emptyContact() {
  return {
    savedName: "", // contactAction.fullName — the name you saved
    externalName: "", // LazyClaw unified contact store (macOS Contacts et al.)
    verifiedName: "", // business verified name
    legacyName: "", // pre-split contacts.json `name` — provenance unknown, but
    // it is what the user currently sees, so it outranks notify
    notify: "", // pushName — what they call themselves
    subject: "", // group subject (groups only)
    phone: null, // "+34604246401"
    lid: null, // "133913422909630@lid"
    // Derived, never written directly — see mergeContact(). Kept on the record
    // so the many read sites that predate this module keep working, but now
    // resolved by precedence instead of by whichever handler wrote last.
    name: "",
  };
}

const TEXT_FIELDS = ["savedName", "externalName", "verifiedName", "legacyName", "notify", "subject"];
const REF_FIELDS = ["phone", "lid"];

/**
 * Merge a partial patch onto an existing record, returning a NEW record.
 *
 * Upgrade-only: a field is written only when the incoming value is non-empty.
 * A partial event can therefore never erase a better value that arrived
 * earlier — the destructive `contacts.set(jid, {...})` this replaces was the
 * reason saved names kept reverting to pushNames.
 */
function mergeContact(existing, incoming) {
  const base = existing || emptyContact();
  const patch = incoming || {};
  const out = { ...emptyContact(), ...base };
  for (const f of TEXT_FIELDS) {
    if (patch[f]) out[f] = patch[f];
  }
  for (const f of REF_FIELDS) {
    if (patch[f]) out[f] = patch[f];
  }
  out.name = bestName(out);
  return out;
}

/**
 * Resolve the single name to show for a contact.
 *
 * Groups only ever use their subject — a group must never inherit a
 * participant's pushName or fall back to its raw JID.
 */
function displayName(record, jid) {
  const c = record || emptyContact();
  if (isGroupJid(jid)) return c.subject || "";
  const named = c.savedName || c.externalName || c.verifiedName || c.legacyName || c.notify;
  if (named) return named;
  if (c.phone) return c.phone;
  return jid ? String(jid).split("@")[0] : "";
}

/** The best human-chosen name, ignoring phone/JID fallbacks. "" when unknown. */
function bestName(record) {
  const c = record || emptyContact();
  return (
    c.savedName || c.externalName || c.verifiedName || c.legacyName || c.notify || c.subject || ""
  );
}

/**
 * Normalize a Baileys `Contact` into a merge patch.
 *
 * Baileys 6.7.21 emits the saved name together with the authoritative lid↔jid
 * pair from the contactAction branch of app-state sync (chat-utils.js:650):
 *
 *   { id, name: contactAction.fullName, lid: contactAction.lidJid, jid }
 *
 * `id` may be either a phone JID or a LID, so the phone must be read from
 * `c.jid` first — reading it from `c.id` is what blanked the number column for
 * every LID-addressed contact.
 */
function fromBaileysContact(c) {
  if (!c) return {};
  const phoneJid = c.jid || (String(c.id || "").endsWith(PHONE_SUFFIX) ? c.id : null);
  const lid = c.lid || (String(c.id || "").endsWith(LID_SUFFIX) ? c.id : null);
  return {
    savedName: c.name || "",
    verifiedName: c.verifiedName || "",
    notify: c.notify || "",
    phone: extractPhone(phoneJid),
    lid: lid || null,
  };
}

/**
 * Normalize a Baileys `Chat` into a merge patch.
 *
 * A group's `name` is its subject and is authoritative. A DM's `name` has no
 * declared provenance — it may be a saved name or the peer's pushName — so it
 * is treated as pushName-quality. The critical_unblock_low resync supplies the
 * real saved name shortly after connect and takes precedence over it.
 */
function fromBaileysChat(chat) {
  if (!chat || !chat.id) return {};
  if (isGroupJid(chat.id)) return { subject: chat.name || "" };
  return { notify: chat.name || "", phone: extractPhone(chat.id) };
}

/**
 * Build bidirectional LID↔phone-JID maps from the contact store.
 *
 * Pass 1 uses the authoritative pair WhatsApp itself supplies. Pass 2 keeps the
 * legacy "one phone + one LID share a unique name" heuristic for contacts that
 * predate the authoritative data, but only where pass 1 found nothing — and it
 * refuses to guess on blank names or ambiguous collisions.
 */
function buildLidMap(contacts) {
  const lidToPhone = new Map();
  const phoneToLid = new Map();

  const link = (lidJid, pnJid) => {
    if (!lidJid || !pnJid) return;
    if (lidToPhone.has(lidJid) || phoneToLid.has(pnJid)) return;
    lidToPhone.set(lidJid, pnJid);
    phoneToLid.set(pnJid, lidJid);
  };

  // Pass 1 — authoritative pairs from WhatsApp.
  for (const [jid, rec] of contacts) {
    if (!rec) continue;
    if (jid.endsWith(PHONE_SUFFIX) && rec.lid) {
      link(rec.lid, normalizeJid(jid));
    } else if (jid.endsWith(LID_SUFFIX)) {
      const pn = phoneToJid(rec.phone);
      if (pn) link(jid, pn);
    }
  }

  // Pass 2 — legacy name heuristic, only for what pass 1 left unmapped.
  const byName = new Map();
  for (const [jid, rec] of contacts) {
    const name = bestName(rec).toLowerCase().trim();
    if (!name) continue; // never collapse unnamed contacts into one class
    if (!byName.has(name)) byName.set(name, []);
    byName.get(name).push(jid);
  }
  for (const [, jids] of byName) {
    const phones = jids.filter((j) => j.endsWith(PHONE_SUFFIX) && !phoneToLid.has(j));
    const lids = jids.filter((j) => j.endsWith(LID_SUFFIX) && !lidToPhone.has(j));
    if (phones.length === 1 && lids.length === 1) link(lids[0], phones[0]);
  }

  return { lidToPhone, phoneToLid };
}

/** Bumped whenever the persisted contact shape changes. */
const CONTACT_SCHEMA_VERSION = 2;

/**
 * Shrink a record for persistence: drop the derived `name` and every empty
 * field, keeping an explicit version marker.
 *
 * Serializing the full record grew contacts.json from 346K to 850K on a
 * 5,668-entry store — mostly `""` and `null` — and it is rewritten (debounced)
 * on every contact event.
 */
function packContact(record) {
  const out = { v: CONTACT_SCHEMA_VERSION };
  for (const f of [...TEXT_FIELDS, ...REF_FIELDS]) {
    if (record && record[f]) out[f] = record[f];
  }
  return out;
}

/**
 * Migrate a record persisted under the old `{ name, notify, phone }` shape.
 *
 * The legacy `name` is provenance-free — it may be a saved name or a pushName —
 * so it is demoted to `notify` (the pessimistic reading) rather than promoted
 * into `savedName`. Group records keep it as the subject, which was always
 * authoritative. The first `critical_unblock_low` resync after reconnect fills
 * in the real saved names, so the store self-heals on the next connect.
 */
function migrateLegacyContact(legacy, jid) {
  if (!legacy) return emptyContact();
  // The version marker is explicit: packContact() omits empty fields, so
  // sniffing for the presence of `savedName`/`subject` would misread a
  // freshly-packed record with neither as a legacy one.
  if (legacy.v >= CONTACT_SCHEMA_VERSION) {
    return mergeContact(emptyContact(), legacy);
  }
  const patch = {
    notify: legacy.notify || "",
    phone: legacy.phone || extractPhone(jid),
    lid: legacy.lid || null,
  };
  if (isGroupJid(jid)) {
    patch.subject = legacy.name || "";
  } else if (legacy.name && legacy.name !== legacy.phone && legacy.name !== patch.phone) {
    // Kept above notify, not merged into it: cache rows routinely hold a
    // different `name` and `notify` (e.g. "Solvår Alicia" / "Vato") and every
    // old read site resolved `name || notify`, so folding it into notify would
    // silently rename those contacts. A real savedName still overrides it.
    patch.legacyName = legacy.name;
  }
  return mergeContact(emptyContact(), patch);
}

module.exports = {
  extractPhone,
  phoneToJid,
  normalizeJid,
  isGroupJid,
  emptyContact,
  mergeContact,
  displayName,
  bestName,
  fromBaileysContact,
  fromBaileysChat,
  packContact,
  CONTACT_SCHEMA_VERSION,
  buildLidMap,
  migrateLegacyContact,
};
