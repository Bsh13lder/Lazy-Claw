"use strict";

// One-shot recovery of WhatsApp saved contact names.
//
// `Contact.name` — the name YOU saved — reaches Baileys only through the
// `contactAction` sync-action, which ships exclusively in the
// `critical_unblock_low` app-state collection. Requesting that collection in
// resyncAppState() is necessary but not sufficient: resyncAppState sends the
// LOCALLY STORED version and the server replies with patches *since* it. A
// collection already at v16 from the original pairing sync returns nothing, so
// no `contacts.upsert` fires and the names never arrive — they were delivered
// once, at pairing, and dropped by the handler of the day.
//
// Clearing the stored version forces WhatsApp to send a full snapshot. Baileys
// does exactly this internally when a patch fails to decode
// (Socket/chats.js → `authState.keys.set({'app-state-sync-version': {[name]: null}})`).
// A marker file keeps it to a single occurrence: re-downloading the snapshot on
// every boot would be wasteful and would fight the normal incremental sync.

const fs = require("node:fs");
const path = require("node:path");

/** The collection carrying contactAction (saved names) and the blocklist. */
const NAMES_COLLECTION = "critical_unblock_low";

/** Bump the suffix to re-run the backfill after a change that needs it. */
const BACKFILL_MARKER = ".saved-names-backfilled-v1";

/** Path of Baileys' stored sync version for a collection. */
function versionFileFor(authDir, collection) {
  return path.join(authDir, `app-state-sync-version-${collection}.json`);
}

/**
 * Pure decision — should the stored version be cleared?
 *
 * A fresh pairing has no stored version and will sync the collection from v0
 * on its own, so it is marked done without touching anything. Doing otherwise
 * would delete a perfectly good version on the next boot.
 */
function savedNameBackfillPlan({ markerExists, versionExists }) {
  if (markerExists) return { backfill: false, reason: "already-done" };
  if (!versionExists) return { backfill: false, reason: "no-stored-version" };
  return { backfill: true, reason: "stored-version-blocks-resync" };
}

/**
 * Apply the plan against an auth directory. Idempotent and non-throwing —
 * a failure here must never stop the socket from connecting.
 */
function runSavedNameBackfill(authDir) {
  if (!authDir || !fs.existsSync(authDir)) {
    return { backfill: false, reason: "no-auth-dir" };
  }
  const marker = path.join(authDir, BACKFILL_MARKER);
  const versionFile = versionFileFor(authDir, NAMES_COLLECTION);
  const plan = savedNameBackfillPlan({
    markerExists: fs.existsSync(marker),
    versionExists: fs.existsSync(versionFile),
  });

  try {
    if (plan.backfill) fs.rmSync(versionFile, { force: true });
    // Written in both branches: "fresh pairing" is also a terminal state.
    fs.writeFileSync(marker, new Date().toISOString());
  } catch (e) {
    return { backfill: false, reason: `failed: ${e.message}` };
  }
  return plan;
}

module.exports = {
  NAMES_COLLECTION,
  BACKFILL_MARKER,
  versionFileFor,
  savedNameBackfillPlan,
  runSavedNameBackfill,
};
