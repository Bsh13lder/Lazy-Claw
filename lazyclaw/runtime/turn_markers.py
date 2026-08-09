"""Shared turn-marker constants — single source of truth.

``BACKGROUND_TURN_PREFIXES`` are the message prefixes the heartbeat
daemon stamps on internally generated turns (``heartbeat/daemon.py``
enqueues ``[JOB:{name}] ...``; watchers/reminders use the sibling
prefixes). Two consumers key off them:

* ``runtime/agent.py`` — routes such turns onto the BACKGROUND browser
  lane so a cron/watcher browser action can't steal the foreground tab;
* ``gateway/routes/chat_history.py`` — tags persisted user rows with
  ``kind: "cron"`` so clients can label/collapse internal turns.

This tiny module exists so the gateway route can import the tuple
without pulling the full ``runtime/agent.py`` dependency graph into a
request path.
"""

from __future__ import annotations

BACKGROUND_TURN_PREFIXES: tuple[str, ...] = ("[WATCHER:", "[JOB:", "[REMINDER")
