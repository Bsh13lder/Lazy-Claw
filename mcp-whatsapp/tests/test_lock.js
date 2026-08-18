"use strict";

// Pure-helper tests for src/lock.js — run with `node --test tests/`.
//
// 2026-08-18 incident: `make rebuild` recreated the lazyclaw container in
// under 60s. The old mcp-whatsapp heartbeat was <60s old (not stale) and its
// PID — from the PREVIOUS container's PID namespace — happened to belong to
// a live unrelated process in the new container. kill(pid, 0) succeeded, the
// lock looked "legitimately held", and the new process ran cache-only
// FOREVER: whatsapp_read served cache, every whatsapp_send failed with
// "WhatsApp not connected". A lock written by a different hostname (Docker
// hostname = container id, new on every recreate) cannot have a live holder
// in this container, so it must be takeover-able regardless of age or PID.

const test = require("node:test");
const assert = require("node:assert/strict");

const { lockDecision, STALE_MS } = require("../src/lock.js");

const NOW = 1_700_000_000_000;
const ENV = {
  now: NOW,
  pid: 100,
  hostname: "container-B",
  isPidAlive: () => true,
};

test("no lock file → takeover", () => {
  assert.deepEqual(lockDecision(null, ENV), { takeover: true, reason: "no-lock" });
  assert.deepEqual(lockDecision(undefined, ENV), { takeover: true, reason: "no-lock" });
  assert.equal(lockDecision("garbage", ENV).takeover, true);
});

test("stale heartbeat → takeover regardless of pid/hostname", () => {
  const lock = { pid: 42, time: NOW - STALE_MS, hostname: "container-B" };
  assert.deepEqual(lockDecision(lock, ENV), { takeover: true, reason: "stale" });
});

test("fresh lock from ANOTHER hostname → takeover (the rebuild incident)", () => {
  // Fresh heartbeat + alive PID — but written by the previous container.
  const lock = { pid: 42, time: NOW - 10_000, hostname: "container-A" };
  const d = lockDecision(lock, { ...ENV, isPidAlive: () => true });
  assert.deepEqual(d, { takeover: true, reason: "other-container" });
});

test("fresh lock, same hostname, holder alive → held (440-loop prevention)", () => {
  const lock = { pid: 42, time: NOW - 10_000, hostname: "container-B" };
  assert.deepEqual(
    lockDecision(lock, { ...ENV, isPidAlive: () => true }),
    { takeover: false, reason: "held" },
  );
});

test("fresh lock, same hostname, holder dead → takeover", () => {
  const lock = { pid: 42, time: NOW - 10_000, hostname: "container-B" };
  assert.deepEqual(
    lockDecision(lock, { ...ENV, isPidAlive: () => false }),
    { takeover: true, reason: "dead-pid" },
  );
});

test("legacy lock without hostname falls back to the PID probe", () => {
  const lock = { pid: 42, time: NOW - 10_000 };
  assert.equal(lockDecision(lock, { ...ENV, isPidAlive: () => true }).takeover, false);
  assert.equal(lockDecision(lock, { ...ENV, isPidAlive: () => false }).takeover, true);
});

test("our own pid in the lock → takeover", () => {
  const lock = { pid: 100, time: NOW - 10_000, hostname: "container-B" };
  assert.deepEqual(lockDecision(lock, ENV), { takeover: true, reason: "self" });
});
