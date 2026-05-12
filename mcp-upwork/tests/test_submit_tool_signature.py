"""Guard test: upwork_submit_proposal MCP tool must expose ALL fields
of SubmitProposalParams, not just the cover-letter/rate/bid subset.

Before this guard:
  - SubmitProposalParams had connects_to_send / milestone_due_date /
    project_duration (model fields + form-fill implementation)
  - The MCP tool wrapper in server.py only took job_url/cover_letter/
    rate/bid/answers and silently DROPPED the rest.
  - Every caller (lazyclaw runtime, MiniMax brain, a direct stdio
    JSON-RPC client) submitted at Upwork's auto-suggested connects
    default — no boost, no cap, no scheduled milestone control.

This test fails if anyone re-narrows the wrapper signature, which is
silent-degrade behavior we can't catch any other way.
"""

from __future__ import annotations

import inspect

from upwork_mcp.server import upwork_submit_proposal
from upwork_mcp.tools.proposals import SubmitProposalParams


def test_mcp_submit_tool_exposes_all_model_fields():
    tool_params = set(inspect.signature(upwork_submit_proposal).parameters)
    model_fields = set(SubmitProposalParams.model_fields.keys())
    missing = model_fields - tool_params
    assert not missing, (
        f"upwork_submit_proposal MCP signature is missing model fields "
        f"{sorted(missing)} — every caller will silently use defaults "
        f"for these fields. Re-add them to server.py."
    )


def test_mcp_submit_tool_exposes_connects_to_send_specifically():
    """Sticky test: the specific field the user complained about ('didn't
    bid connects') must always be on the tool surface."""
    assert "connects_to_send" in inspect.signature(upwork_submit_proposal).parameters
