"""search_tools must not surface a Google duplicate over the native tool.

When both a native `create_sheet` and a Google Workspace `create_sheet` match
a query, discovery returns ONLY the native one. A Google-specific tool with a
distinct name (`create_google_sheet`) still surfaces — the explicit-Google
path is preserved.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lazyclaw.skills.builtin.tool_discovery import SearchToolsSkill


@pytest.mark.asyncio
async def test_search_tools_prefers_native_sheet_over_google_duplicate():
    reg = MagicMock()
    reg.list_core_tools.return_value = [
        {"function": {"name": "create_sheet", "description": "native encrypted spreadsheet"}},
    ]
    reg.list_mcp_tools.return_value = [
        {"function": {"name": "mcp_ce2f_create_sheet", "description": "Google Sheets create spreadsheet"}},
        {"function": {"name": "mcp_ce2f_create_google_sheet", "description": "Google Sheets via run_task spreadsheet"}},
    ]
    skill = SearchToolsSkill(registry=reg)
    out = await skill.execute("u1", {"query": "create sheet spreadsheet"})
    assert "**create_sheet**" in out
    assert "mcp_ce2f_create_sheet" not in out
    # Distinct-name Google tool still discoverable for explicit requests.
    assert "mcp_ce2f_create_google_sheet" in out
