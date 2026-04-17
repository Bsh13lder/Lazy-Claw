"""Watcher observability — in-memory per-check history ring.

Watcher *jobs* still live in agent_jobs (encrypted context JSON). This
package sits next to that and stores the last N polls per watcher so the
Web UI / NL skills can answer 'what did it find this time?' without
bloating the DB with 1440 rows/day per watcher.
"""
