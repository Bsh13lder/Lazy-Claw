"""tools: '*' gives a specialist every tool except dispatch tools."""
from lazyclaw.teams.runner import WILDCARD_DENYLIST, WILDCARD_TOOLS, _filter_tools
from lazyclaw.teams.specialist_loader import validate_specialist_tools
from lazyclaw.teams.specialist import SpecialistConfig


def _tool(name: str, desc: str = "") -> dict:
    return {"function": {"name": name, "description": desc}}


class FakeRegistry:
    def __init__(self, tools, mcp_tools=()):
        self._tools = list(tools)
        self._mcp = list(mcp_tools)

    def list_tools(self):
        return list(self._tools)

    def list_mcp_tools(self):
        return list(self._mcp)


def test_wildcard_includes_all_native_minus_denylist():
    reg = FakeRegistry([
        _tool("web_search"), _tool("browser"),
        _tool("agent"), _tool("delegate"),
        _tool("dispatch_subagents"), _tool("run_background"),
    ])
    out = _filter_tools(reg, (WILDCARD_TOOLS,))
    names = {t["function"]["name"] for t in out}
    assert names == {"web_search", "browser"}
    assert names.isdisjoint(WILDCARD_DENYLIST)


def test_wildcard_unions_mcp_tools():
    reg = FakeRegistry(
        [_tool("web_search")],
        mcp_tools=[_tool("mcp_abc123_upwork_get_messages")],
    )
    out = _filter_tools(reg, (WILDCARD_TOOLS,))
    names = {t["function"]["name"] for t in out}
    assert "mcp_abc123_upwork_get_messages" in names


def test_wildcard_no_duplicates():
    reg = FakeRegistry(
        [_tool("web_search")],
        mcp_tools=[_tool("web_search")],
    )
    out = _filter_tools(reg, (WILDCARD_TOOLS,))
    assert len(out) == 1


def test_exact_allowlist_unchanged():
    reg = FakeRegistry([_tool("web_search"), _tool("browser")])
    out = _filter_tools(reg, ("web_search",))
    assert [t["function"]["name"] for t in out] == ["web_search"]


def test_validator_exempts_wildcard():
    spec = SpecialistConfig(
        name="gp", display_name="GP", system_prompt="x",
        allowed_skills=(WILDCARD_TOOLS,),
    )
    assert validate_specialist_tools([spec], ["web_search"]) == {}
