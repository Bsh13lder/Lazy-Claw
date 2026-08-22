"use strict";

// Pure-helper tests for src/contacts.js — run with `node --test tests/`.
//
// 2026-08-22 incident: WhatsApp stopped showing saved contact names and phone
// numbers. Three compounding causes, all reproduced below:
//
//   1. The contact record had ONE `name` field written by four sources with no
//      precedence (contactAction.fullName / verifiedName / pushName / phone).
//      Once a pushName landed in `name`, it was indistinguishable from a saved
//      name and could never be upgraded when the real one arrived.
//   2. Every handler did `contacts.set(jid, {...})` — a destructive replace. A
//      later partial event (e.g. presence-only upsert carrying just `notify`)
//      wiped an already-synced saved name.
//   3. Baileys emits the saved name AND the authoritative lid↔jid pair together
//      in the `contactAction` branch of app-state sync (chat-utils.js:650), but
//      that action only ships in the `critical_unblock_low` collection, which
//      the resync call never requested. `c.lid` was discarded everywhere, so
//      LID↔phone fell back to a "two contacts share a name" guess.

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  extractPhone,
  normalizeJid,
  emptyContact,
  mergeContact,
  displayName,
  buildLidMap,
  fromBaileysContact,
  migrateLegacyContact,
  fromBaileysChat,
  packContact,
} = require("../src/contacts.js");

const PN = "34604246401@s.whatsapp.net";
const LID = "133913422909630@lid";
const GROUP = "120363001234567890@g.us";

// ---------------------------------------------------------------------------
// extractPhone / normalizeJid
// ---------------------------------------------------------------------------

test("extractPhone returns +number for phone JIDs", () => {
  assert.equal(extractPhone(PN), "+34604246401");
  assert.equal(extractPhone("34604246401:18@s.whatsapp.net"), "+34604246401");
});

test("extractPhone returns null for LID and group JIDs", () => {
  assert.equal(extractPhone(LID), null);
  assert.equal(extractPhone(GROUP), null);
  assert.equal(extractPhone(""), null);
  assert.equal(extractPhone(null), null);
});

test("normalizeJid strips the device suffix from phone JIDs only", () => {
  assert.equal(normalizeJid("34604246401:18@s.whatsapp.net"), PN);
  assert.equal(normalizeJid(GROUP), GROUP);
  assert.equal(normalizeJid(LID), LID);
});

// ---------------------------------------------------------------------------
// mergeContact — the fix for destructive overwrite
// ---------------------------------------------------------------------------

test("mergeContact never lets a weaker source clobber a saved name", () => {
  const saved = mergeContact(emptyContact(), { savedName: "Buchvardi" });
  // A later presence/pushName-only upsert must NOT destroy the saved name.
  const after = mergeContact(saved, { notify: "buchi 🔥" });
  assert.equal(after.savedName, "Buchvardi");
  assert.equal(after.notify, "buchi 🔥");
});

test("mergeContact does not overwrite a populated field with an empty one", () => {
  const existing = mergeContact(emptyContact(), {
    savedName: "Ana",
    phone: "+34600000000",
    lid: LID,
  });
  const after = mergeContact(existing, { savedName: "", phone: null, lid: undefined });
  assert.equal(after.savedName, "Ana");
  assert.equal(after.phone, "+34600000000");
  assert.equal(after.lid, LID);
});

test("mergeContact upgrades an empty field when a better value arrives", () => {
  const seen = mergeContact(emptyContact(), { notify: "unknown guy" });
  assert.equal(seen.savedName, "");
  const named = mergeContact(seen, { savedName: "Real Name", lid: LID });
  assert.equal(named.savedName, "Real Name");
  assert.equal(named.notify, "unknown guy");
  assert.equal(named.lid, LID);
});

test("mergeContact is immutable — it never mutates its inputs", () => {
  const existing = mergeContact(emptyContact(), { savedName: "Ana" });
  const snapshot = JSON.stringify(existing);
  const result = mergeContact(existing, { notify: "anita" });
  assert.equal(JSON.stringify(existing), snapshot, "existing was mutated");
  assert.notEqual(result, existing, "should return a new object");
});

test("mergeContact derives .name so legacy read sites resolve by precedence", () => {
  // ~50 call sites in index.js read `c.name` directly. It must stay populated,
  // but resolved by precedence rather than by whichever handler wrote last.
  const seen = mergeContact(emptyContact(), { notify: "buchi 🔥" });
  assert.equal(seen.name, "buchi 🔥");
  const named = mergeContact(seen, { savedName: "Buchvardi" });
  assert.equal(named.name, "Buchvardi", "saved name must win once it arrives");
  const later = mergeContact(named, { notify: "new status name" });
  assert.equal(later.name, "Buchvardi", "a later pushName must not win");
});

test("mergeContact ignores a directly supplied .name — it is derived only", () => {
  const c = mergeContact(emptyContact(), { name: "injected", notify: "real" });
  assert.equal(c.name, "real");
});

test("mergeContact derives a group .name from its subject", () => {
  const g = mergeContact(emptyContact(), { subject: "Family" });
  assert.equal(g.name, "Family");
});

// ---------------------------------------------------------------------------
// migrateLegacyContact — reading contacts.json written under the old shape
// ---------------------------------------------------------------------------

test("migrateLegacyContact parks a provenance-free legacy name below savedName", () => {
  const m = migrateLegacyContact({ name: "Buchvardi", notify: "", phone: "+34604246401" }, PN);
  assert.equal(m.savedName, "", "legacy name is not trustworthy as a saved name");
  assert.equal(m.legacyName, "Buchvardi");
  assert.equal(m.name, "Buchvardi", "still displays — just upgradeable now");
  assert.equal(m.phone, "+34604246401");
});

test("migrateLegacyContact keeps a legacy group name as the subject", () => {
  const m = migrateLegacyContact({ name: "Family", notify: "", phone: null }, GROUP);
  assert.equal(m.subject, "Family");
  assert.equal(m.notify, "");
  assert.equal(displayName(m, GROUP), "Family");
});

test("migrateLegacyContact drops a legacy name that was just the phone number", () => {
  const m = migrateLegacyContact({ name: "+34604246401", notify: "", phone: "+34604246401" }, PN);
  assert.equal(m.notify, "", "a phone number is not a name");
  assert.equal(displayName(m, PN), "+34604246401", "still shown via the phone fallback");
});

test("migrateLegacyContact keeps displaying the legacy name over a differing notify", () => {
  // Real cache rows carry BOTH a `name` and a different `notify`
  // (e.g. name="Solvår Alicia", notify="Vato"). Every old read site resolved
  // `c.name || c.notify`, so the legacy name is what the user currently sees —
  // demoting it below notify silently renamed 26 real contacts.
  const m = migrateLegacyContact({ name: "Solvår Alicia", notify: "Vato", phone: null }, LID);
  assert.equal(displayName(m, LID), "Solvår Alicia");
  assert.equal(m.notify, "Vato", "the peer's own pushName is still retained");
  assert.equal(m.savedName, "", "still not trusted as a saved name");
});

test("a real saved name still overrides a preserved legacy name", () => {
  const m = migrateLegacyContact({ name: "Solvår Alicia", notify: "Vato" }, PN);
  const healed = mergeContact(m, fromBaileysContact({ id: PN, name: "Alicia Solvar", jid: PN }));
  assert.equal(healed.name, "Alicia Solvar");
});

test("packContact drops empty fields and the derived name, keeping a version marker", () => {
  const c = mergeContact(emptyContact(), { savedName: "Ana", phone: "+34604246401" });
  const packed = packContact(c);
  assert.deepEqual(packed, { v: 2, savedName: "Ana", phone: "+34604246401" });
  assert.equal("name" in packed, false, "derived — must not be persisted");
  assert.equal("notify" in packed, false, "empty — must not be persisted");
});

test("pack → migrate round-trips without loss", () => {
  const original = mergeContact(emptyContact(), {
    savedName: "Ana",
    notify: "anita",
    phone: "+34604246401",
    lid: LID,
  });
  const restored = migrateLegacyContact(JSON.parse(JSON.stringify(packContact(original))), PN);
  assert.deepEqual(restored, original);
});

test("a packed record with no names is not mistaken for a legacy one", () => {
  // packContact() omits empty fields, so an all-empty record serializes to just
  // { v: 2 }. Sniffing for savedName/subject would misread it as pre-migration.
  const bare = mergeContact(emptyContact(), { phone: "+34604246401" });
  const restored = migrateLegacyContact(packContact(bare), PN);
  assert.equal(restored.legacyName, "", "must not re-run the legacy demotion");
  assert.deepEqual(restored, bare);
});

test("migrateLegacyContact is idempotent on already-migrated records", () => {
  const once = migrateLegacyContact({ name: "Ana", phone: "+34604246401" }, PN);
  const twice = migrateLegacyContact(once, PN);
  assert.deepEqual(twice, once);
});

test("a migrated legacy record is upgraded by the first real saved name", () => {
  // This is the self-heal path: after the critical_unblock_low fix, the first
  // reconnect replaces every demoted pushName with the real saved name.
  const legacy = migrateLegacyContact({ name: "buchi 🔥", phone: "+34604246401" }, PN);
  const healed = mergeContact(legacy, fromBaileysContact({ id: PN, name: "Buchvardi", lid: LID, jid: PN }));
  assert.equal(healed.name, "Buchvardi");
  assert.equal(healed.lid, LID);
});

// ---------------------------------------------------------------------------
// displayName — precedence resolution at read time
// ---------------------------------------------------------------------------

test("displayName prefers the saved name over pushName", () => {
  const c = mergeContact(emptyContact(), {
    savedName: "Buchvardi",
    notify: "buchi 🔥",
    verifiedName: "Buch SL",
    phone: "+34604246401",
  });
  assert.equal(displayName(c, PN), "Buchvardi");
});

test("displayName full precedence chain for DMs", () => {
  const base = { phone: "+34604246401" };
  assert.equal(
    displayName({ ...emptyContact(), ...base, notify: "them" }, PN),
    "them",
    "notify beats phone",
  );
  assert.equal(
    displayName({ ...emptyContact(), ...base, notify: "them", verifiedName: "Biz SL" }, PN),
    "Biz SL",
    "verifiedName beats notify",
  );
  assert.equal(
    displayName({ ...emptyContact(), ...base, notify: "them", externalName: "From Contacts" }, PN),
    "From Contacts",
    "unified contact store beats notify",
  );
  assert.equal(
    displayName({ ...emptyContact(), ...base }, PN),
    "+34604246401",
    "phone is the last named fallback",
  );
});

test("displayName falls back to the JID localpart when nothing else is known", () => {
  assert.equal(displayName(emptyContact(), LID), "133913422909630");
});

test("displayName never invents a name for a group from a participant", () => {
  // Groups have no notify/phone — only a subject. A blank subject must stay blank
  // rather than leaking a pushName or the raw group id.
  const g = mergeContact(emptyContact(), { notify: "some participant" });
  assert.equal(displayName(g, GROUP), "");
  const named = mergeContact(g, { subject: "Family" });
  assert.equal(displayName(named, GROUP), "Family");
});

// ---------------------------------------------------------------------------
// fromBaileysContact — reading what Baileys actually sends
// ---------------------------------------------------------------------------

test("fromBaileysContact captures the lid↔jid pair emitted by contactAction", () => {
  // Shape emitted by Baileys 6.7.21 chat-utils.js:650 on critical_unblock_low sync.
  const patch = fromBaileysContact({ id: PN, name: "Buchvardi", lid: LID, jid: PN });
  assert.equal(patch.savedName, "Buchvardi");
  assert.equal(patch.lid, LID);
  assert.equal(patch.phone, "+34604246401");
});

test("fromBaileysContact derives the phone from c.jid when the id is a LID", () => {
  // This is the regression: phone was read from c.id, which is a LID here, so
  // extractPhone() returned null and the number column went blank.
  const patch = fromBaileysContact({ id: LID, name: "Ana", jid: PN });
  assert.equal(patch.phone, "+34604246401");
  assert.equal(patch.savedName, "Ana");
});

test("fromBaileysContact keeps pushName out of the saved-name slot", () => {
  const patch = fromBaileysContact({ id: PN, notify: "them", verifiedName: "Biz SL" });
  assert.equal(patch.savedName, "");
  assert.equal(patch.notify, "them");
  assert.equal(patch.verifiedName, "Biz SL");
});

// ---------------------------------------------------------------------------
// fromBaileysChat
// ---------------------------------------------------------------------------

test("fromBaileysChat maps a group name to subject, never to notify", () => {
  const patch = fromBaileysChat({ id: GROUP, name: "Family" });
  assert.equal(patch.subject, "Family");
  assert.equal(patch.notify, undefined);
});

test("fromBaileysChat treats a DM chat name as pushName-quality", () => {
  // Provenance is undeclared, so it must not occupy the saved-name slot — the
  // critical_unblock_low resync has to be able to override it.
  const patch = fromBaileysChat({ id: PN, name: "Maybe Ana" });
  assert.equal(patch.savedName, undefined);
  assert.equal(patch.notify, "Maybe Ana");
  assert.equal(patch.phone, "+34604246401");

  const rec = mergeContact(emptyContact(), patch);
  const healed = mergeContact(rec, fromBaileysContact({ id: PN, name: "Ana Real", jid: PN }));
  assert.equal(healed.name, "Ana Real");
});

test("fromBaileysChat on a blank group does not erase a known subject", () => {
  const known = mergeContact(emptyContact(), { subject: "Family" });
  const after = mergeContact(known, fromBaileysChat({ id: GROUP, name: "" }));
  assert.equal(after.subject, "Family");
});

// ---------------------------------------------------------------------------
// Full sequence — the bug lived in the ordering, not in any single function
// ---------------------------------------------------------------------------

test("regression: the real connect sequence ends with the saved name and phone", () => {
  const store = new Map();
  const put = (jid, patch) => store.set(jid, mergeContact(store.get(jid), patch));

  // 1. Boot: a legacy contacts.json holding only the peer's own pushName.
  store.set(PN, migrateLegacyContact({ name: "buchi 🔥", notify: "", phone: "+34604246401" }, PN));

  // 2. History sync delivers the chat, addressed by LID, with no useful name.
  put(LID, fromBaileysChat({ id: LID, name: "" }));

  // 3. A message arrives on the LID chat carrying only the sender's pushName.
  put(LID, { notify: "buchi 🔥" });

  // Before the resync there is no saved name anywhere and the LID has no phone.
  assert.equal(store.get(PN).savedName, "");
  assert.equal(store.get(LID).phone, null);

  // 4. resyncAppState(["critical_unblock_low", ...]) → contacts.upsert with the
  //    saved name AND the authoritative lid↔jid pair. This is the step that was
  //    missing entirely.
  put(PN, fromBaileysContact({ id: PN, name: "Buchvardi", lid: LID, jid: PN }));

  const { lidToPhone } = buildLidMap(store);
  assert.equal(store.get(PN).name, "Buchvardi", "saved name wins over pushName");
  assert.equal(store.get(PN).phone, "+34604246401");
  assert.equal(lidToPhone.get(LID), PN, "LID chat resolves to the phone JID");

  // 5. A later message on the LID chat must not undo any of it.
  put(LID, { notify: "buchi 🔥" });
  assert.equal(store.get(PN).name, "Buchvardi");
  assert.equal(lidToPhone.get(LID), PN);
});

test("regression: a group never adopts a participant's pushName", () => {
  const store = new Map();
  const put = (jid, patch) => store.set(jid, mergeContact(store.get(jid), patch));

  put(GROUP, fromBaileysChat({ id: GROUP, name: "" })); // seen, subject unknown
  put(GROUP, { notify: "some participant" }); // must never leak into the name
  assert.equal(displayName(store.get(GROUP), GROUP), "");

  put(GROUP, { subject: "Family" }); // groupMetadata resolves it
  assert.equal(displayName(store.get(GROUP), GROUP), "Family");
});

// ---------------------------------------------------------------------------
// buildLidMap — authoritative pairs first, name heuristic only as fallback
// ---------------------------------------------------------------------------

test("buildLidMap uses the authoritative lid field, not a name guess", () => {
  const contacts = new Map([
    [PN, mergeContact(emptyContact(), { savedName: "Ana", phone: "+34604246401", lid: LID })],
  ]);
  const { lidToPhone, phoneToLid } = buildLidMap(contacts);
  assert.equal(lidToPhone.get(LID), PN);
  assert.equal(phoneToLid.get(PN), LID);
});

test("buildLidMap links a LID-keyed record back to its phone JID", () => {
  const contacts = new Map([
    [LID, mergeContact(emptyContact(), { savedName: "Ana", phone: "+34604246401" })],
  ]);
  const { lidToPhone } = buildLidMap(contacts);
  assert.equal(lidToPhone.get(LID), PN);
});

test("buildLidMap resolves same-name contacts that the old heuristic could not", () => {
  // Two different people both saved as "Ana" — the name-collision heuristic
  // linked neither. With authoritative lid fields both resolve correctly.
  const LID2 = "999888777@lid";
  const PN2 = "34600111222@s.whatsapp.net";
  const contacts = new Map([
    [PN, mergeContact(emptyContact(), { savedName: "Ana", phone: "+34604246401", lid: LID })],
    [PN2, mergeContact(emptyContact(), { savedName: "Ana", phone: "+34600111222", lid: LID2 })],
  ]);
  const { lidToPhone } = buildLidMap(contacts);
  assert.equal(lidToPhone.get(LID), PN);
  assert.equal(lidToPhone.get(LID2), PN2);
});

test("buildLidMap falls back to the name heuristic when no lid field is present", () => {
  const contacts = new Map([
    [PN, mergeContact(emptyContact(), { savedName: "Solo Person", phone: "+34604246401" })],
    [LID, mergeContact(emptyContact(), { savedName: "Solo Person" })],
  ]);
  const { lidToPhone } = buildLidMap(contacts);
  assert.equal(lidToPhone.get(LID), PN, "unique name pair should still link");
});

test("buildLidMap heuristic refuses ambiguous name collisions", () => {
  const contacts = new Map([
    [PN, mergeContact(emptyContact(), { savedName: "Ana", phone: "+34604246401" })],
    ["34600111222@s.whatsapp.net", mergeContact(emptyContact(), { savedName: "Ana", phone: "+34600111222" })],
    [LID, mergeContact(emptyContact(), { savedName: "Ana" })],
  ]);
  const { lidToPhone } = buildLidMap(contacts);
  assert.equal(lidToPhone.has(LID), false, "two candidates → must not guess");
});

test("buildLidMap ignores blank names in the heuristic", () => {
  // Bug 1 left most names empty; a blank-name group must never collapse into
  // one giant equivalence class.
  const contacts = new Map([
    [PN, emptyContact()],
    [LID, emptyContact()],
  ]);
  const { lidToPhone } = buildLidMap(contacts);
  assert.equal(lidToPhone.size, 0);
});
