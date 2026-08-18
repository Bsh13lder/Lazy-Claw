"use strict";

// Lock-takeover decision for the single-connection WhatsApp guard.
//
// Why hostname matters (2026-08-18 incident): a `make rebuild` recreates the
// container in under 60s, so the previous process's heartbeat is NOT stale
// yet, and its recorded PID — from the previous container's PID namespace —
// can belong to a live, unrelated process in the new container. The
// kill(pid, 0) probe then succeeds and the lock looks "legitimately held",
// leaving the new process cache-only forever (reads work, every send fails
// with "WhatsApp not connected"). Docker sets the hostname to the container
// id, which changes on every recreate — a lock written by a different
// hostname cannot have a live holder in this container, so it is
// takeover-able regardless of age or PID.

// Stale after two missed 30s heartbeats.
const STALE_MS = 60000;

/**
 * Decide whether an existing lock entitles the current process to take over.
 *
 * @param {object|null} lockData parsed lock-file JSON ({pid, time, hostname?})
 * @param {{now: number, pid: number, hostname: string,
 *          isPidAlive: (pid: number) => boolean}} env
 * @returns {{takeover: boolean, reason: string}}
 */
function lockDecision(lockData, { now, pid, hostname, isPidAlive }) {
  if (!lockData || typeof lockData !== "object") {
    return { takeover: true, reason: "no-lock" };
  }
  const age = now - (lockData.time || 0);
  if (age >= STALE_MS) {
    return { takeover: true, reason: "stale" };
  }
  if (lockData.hostname && hostname && lockData.hostname !== hostname) {
    return { takeover: true, reason: "other-container" };
  }
  const otherPid = lockData.pid;
  if (!otherPid || otherPid === pid) {
    return { takeover: true, reason: "self" };
  }
  if (!isPidAlive(otherPid)) {
    return { takeover: true, reason: "dead-pid" };
  }
  return { takeover: false, reason: "held" };
}

module.exports = { lockDecision, STALE_MS };
