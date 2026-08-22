"use strict";

// Tests for src/app_state.js — run with `node --test "tests/*.js"`.
//
// 2026-08-22 follow-up: adding `critical_unblock_low` to resyncAppState() was
// necessary but NOT sufficient. resyncAppState sends the locally stored version
// and WhatsApp replies with patches *since* it. The collection was already at
// v16 from the original pairing sync, so the server returned nothing and zero
// contacts.upsert events fired — the saved names had been delivered exactly
// once, long ago, and discarded by the handler of the day.
//
// Forcing a full snapshot means clearing the stored version once. Baileys does
// the same thing internally when a patch fails to decode
// (Socket/chats.js: `authState.keys.set({'app-state-sync-version': {[name]: null}})`).

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const {
  BACKFILL_MARKER,
  versionFileFor,
  savedNameBackfillPlan,
  runSavedNameBackfill,
} = require("../src/app_state.js");

function tmpAuthDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "wa-auth-"));
}

test("versionFileFor builds the Baileys auth filename", () => {
  assert.equal(
    path.basename(versionFileFor("/auth", "critical_unblock_low")),
    "app-state-sync-version-critical_unblock_low.json",
  );
});

// ---------------------------------------------------------------------------
// savedNameBackfillPlan — the pure decision
// ---------------------------------------------------------------------------

test("backfills when a stored version exists and the marker does not", () => {
  const plan = savedNameBackfillPlan({ markerExists: false, versionExists: true });
  assert.equal(plan.backfill, true);
  assert.equal(plan.reason, "stored-version-blocks-resync");
});

test("does not backfill twice", () => {
  const plan = savedNameBackfillPlan({ markerExists: true, versionExists: true });
  assert.equal(plan.backfill, false);
  assert.equal(plan.reason, "already-done");
});

test("does not backfill a fresh pairing — it syncs from v0 anyway", () => {
  const plan = savedNameBackfillPlan({ markerExists: false, versionExists: false });
  assert.equal(plan.backfill, false);
  assert.equal(plan.reason, "no-stored-version");
});

// ---------------------------------------------------------------------------
// runSavedNameBackfill — the file effect, exercised on a temp auth dir
// ---------------------------------------------------------------------------

test("runSavedNameBackfill removes the version file and writes the marker", () => {
  const dir = tmpAuthDir();
  const vf = versionFileFor(dir, "critical_unblock_low");
  fs.writeFileSync(vf, JSON.stringify({ version: 16 }));

  const first = runSavedNameBackfill(dir);
  assert.equal(first.backfill, true);
  assert.equal(fs.existsSync(vf), false, "stored version must be cleared");
  assert.equal(fs.existsSync(path.join(dir, BACKFILL_MARKER)), true);

  // Second boot must be a no-op — never re-download the snapshot on every start.
  fs.writeFileSync(vf, JSON.stringify({ version: 20 }));
  const second = runSavedNameBackfill(dir);
  assert.equal(second.backfill, false);
  assert.equal(second.reason, "already-done");
  assert.equal(fs.existsSync(vf), true, "a later version must survive");

  fs.rmSync(dir, { recursive: true, force: true });
});

test("runSavedNameBackfill marks a fresh pairing done without touching anything", () => {
  const dir = tmpAuthDir();
  const res = runSavedNameBackfill(dir);
  assert.equal(res.backfill, false);
  assert.equal(res.reason, "no-stored-version");
  assert.equal(
    fs.existsSync(path.join(dir, BACKFILL_MARKER)),
    true,
    "marker still written so a later boot does not wipe a good version",
  );
  fs.rmSync(dir, { recursive: true, force: true });
});

test("runSavedNameBackfill is safe when the auth dir does not exist", () => {
  const missing = path.join(os.tmpdir(), "wa-auth-does-not-exist-" + process.pid);
  const res = runSavedNameBackfill(missing);
  assert.equal(res.backfill, false);
  assert.equal(res.reason, "no-auth-dir");
});
