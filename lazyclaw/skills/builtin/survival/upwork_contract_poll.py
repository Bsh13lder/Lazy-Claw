"""Poll Upwork contracts every 6h, detect new ones, dispatch intake.

Fired by the ``survival_contract_intake`` cron (registered in
``mode_skill.py``). Calls ``upwork_get_contracts(status='active')`` via
the existing MCP bridge, diff-checks against
``users.settings.survival.seen_contract_urls`` (FIFO bounded 100), and
for every newly-seen contract dispatches the ``new_contract_intake``
skill so the brain batch-asks the user the missing inputs via Telegram.

The intake skill is best-effort: if it fails (e.g. classifier LLM down,
Goal Executor error), the contract is STILL marked seen so we don't
re-dispatch every 6h. The poll report surfaces both successes and
failures so the operator sees what happened.

Honors the heartbeat ``[SILENT]`` prefix when there are no new
contracts so the every-6h tick doesn't spam an empty inbox.

Manual usage: ``check my upwork contracts`` (instant-dispatch route),
or ``upwork_contract_poll(dry_run=True)`` to scan without marking seen
or dispatching intake.
"""

from __future__ import annotations

import json
import logging
from typing import Iterable

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class UpworkContractPollSkill(BaseSkill):
    """Detect new Upwork contracts and surface them to the user.

    Cron-fired (every 6h via ``survival_contract_intake``). Also
    callable manually: "check my upwork contracts".
    """

    def __init__(self, config=None, registry=None) -> None:
        self._config = config
        self._registry = registry

    @property
    def name(self) -> str:
        return "upwork_contract_poll"

    @property
    def description(self) -> str:
        return (
            "Poll Upwork for active contracts, detect any that haven't been "
            "processed yet (deduped against survival.seen_contract_urls), "
            "and report. Fired every 6 hours by the "
            "survival_contract_intake cron; also callable on demand: "
            "'check my upwork contracts'."
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def permission_hint(self) -> str:
        return "allow"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max active contracts to scan. Default 20.",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": (
                        "When true, report new contracts but do NOT mark "
                        "them as seen. Useful for re-testing the cron. "
                        "Default false."
                    ),
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if self._registry is None:
            return "Error: skill registry not available."

        limit = int(params.get("limit") or 20)
        dry_run = bool(params.get("dry_run") or False)

        get_contracts = self._find_tool("upwork_get_contracts")
        if get_contracts is None:
            return (
                "Cannot poll Upwork contracts — mcp-upwork tools not "
                "registered. Make sure the upwork MCP is running and "
                "you're logged in to Upwork in your Brave."
            )

        try:
            raw = await get_contracts.execute(
                user_id, {"status": "active", "limit": limit},
            )
        except Exception as exc:
            logger.exception("upwork_get_contracts call raised")
            return f"Failed to fetch Upwork contracts: {exc}"

        contracts = _normalize_contracts(raw)
        if not contracts:
            # [SILENT] suppresses the Telegram push on heartbeat ticks
            # (telegram_notifier strips it + treats as no-news).
            return (
                "[SILENT] Upwork contracts polled. No active contracts "
                "returned by the MCP."
            )

        from lazyclaw.survival.contracts import (
            add_seen_contract_url,
            filter_unseen,
        )
        urls = [c.get("url", "") for c in contracts if c.get("url")]
        new_urls = await filter_unseen(self._config, user_id, urls)
        if not new_urls:
            return (
                f"[SILENT] Upwork contracts polled. "
                f"{len(contracts)} active, 0 new."
            )

        # Build a per-contract summary block. Url-keyed lookup so the
        # report order matches the new-list order.
        by_url = {c.get("url"): c for c in contracts if c.get("url")}
        lines: list[str] = [
            f"🆕 Upwork — {len(new_urls)} new contract(s) detected:",
        ]

        # Resolve the intake skill once. None when not registered (e.g.
        # the registry hasn't finished booting) — we still mark the
        # contract seen + surface a "intake not available" line.
        intake_skill = (
            self._registry.get("new_contract_intake") if self._registry else None
        )

        for url in new_urls:
            row = by_url.get(url) or {}
            title = row.get("title") or "(untitled contract)"
            client = row.get("client_name") or "(unknown client)"
            status = row.get("status") or "active"
            rate = row.get("rate") or ""
            lines.append(
                f"\n• {title} — {client} ({status})"
                + (f" — {rate}" if rate else "")
                + f"\n    {url}"
            )

            if dry_run:
                lines.append("    (dry_run — not marking seen, not dispatching intake)")
                continue

            # Dispatch the intake skill BEFORE marking seen so that if
            # the dispatch raises, we keep the contract on the queue and
            # retry next tick. The skill ITSELF tolerates partial
            # failures (Ollama down → 'other' work_type) so almost any
            # exception here means something deeper is wrong and we
            # want to keep retrying.
            intake_summary = None
            if intake_skill is not None:
                try:
                    intake_summary = await intake_skill.execute(
                        user_id, {"contract_url": url},
                    )
                except Exception as exc:
                    logger.exception(
                        "new_contract_intake raised for %s — will retry "
                        "next tick", url,
                    )
                    lines.append(
                        f"    ⚠️ Intake failed: {exc}. Will retry next tick."
                    )
                    # Skip marking seen so the next tick re-tries.
                    continue
            else:
                lines.append(
                    "    ⚠️ new_contract_intake skill not registered yet — "
                    "marking seen anyway; trigger intake manually with "
                    f"'intake contract {url}'"
                )

            # Intake succeeded (or skill missing). Mark seen so the next
            # cron tick doesn't re-dispatch the same contract.
            try:
                await add_seen_contract_url(self._config, user_id, url)
            except Exception:
                logger.exception(
                    "add_seen_contract_url failed for %s — will re-dispatch "
                    "intake next tick (idempotent, no harm)", url,
                )

            if intake_summary:
                # Indent the intake summary 2 spaces so the per-contract
                # block stays visually grouped.
                indented = "\n".join(
                    f"    {line}" for line in intake_summary.splitlines()
                )
                lines.append(indented)

        return "\n".join(lines)

    # ── helpers ──────────────────────────────────────────────────────

    def _find_tool(self, suffix: str):
        """Locate an MCP-bridged tool by suffix match.

        Same pattern as ``upwork_inbox_check._find_tool`` — the bridge
        names tools ``mcp_<server-uuid>_<tool>`` so a plain suffix
        match finds the right one without hard-coding the UUID.
        """
        if self._registry is None:
            return None
        for tool_info in self._registry.list_mcp_tools():
            func = tool_info.get("function", {})
            tname = (func.get("name") or "").lower()
            if tname.endswith(suffix.lower()) or suffix.lower() in tname:
                tool = self._registry.get(func.get("name", ""))
                if tool is not None:
                    return tool
        return self._registry.get(suffix)


def _normalize_contracts(raw) -> list[dict]:
    """Coerce the MCP response into a list[dict] regardless of shape.

    mcp-upwork has historically returned either a JSON-encoded string
    (when the bridge stringifies) or a python list directly. Some
    paths wrap in ``{"result": [...]}``. This helper tolerates all
    three so a transport change doesn't silently break the cron.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.debug(
                "upwork_get_contracts returned unparseable string: %r",
                raw[:200],
            )
            return []
    if isinstance(raw, dict):
        raw = raw.get("result") or raw.get("contracts") or []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
    return out
