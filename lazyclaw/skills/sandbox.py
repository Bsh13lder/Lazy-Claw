"""AST-validated sandboxed Python execution for code skills."""

from __future__ import annotations

import ast
import asyncio
import logging
from typing import Any, Callable

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class SandboxError(Exception):
    """Raised when code fails validation or execution."""


# ---------------------------------------------------------------------------
# Blocked / allowed lists
# ---------------------------------------------------------------------------

BLOCKED_NODE_TYPES = (
    ast.Import,
    ast.ImportFrom,
    ast.Global,
    ast.Nonlocal,
    ast.ClassDef,
)

BLOCKED_FUNCTION_NAMES = frozenset({
    "__import__", "exec", "eval", "compile",
    "open", "input", "breakpoint",
    "getattr", "setattr", "delattr",
    "globals", "locals", "vars", "dir",
})

BLOCKED_ATTRIBUTE_NAMES = frozenset({
    "__class__", "__subclasses__", "__globals__",
    "__builtins__", "__code__", "__func__",
    "__self__", "__dict__", "__bases__", "__mro__",
    "__import__",
})

SAFE_BUILTINS: dict[str, Any] = {
    "len": len,
    "range": range,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "sorted": sorted,
    "reversed": reversed,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "round": round,
    "isinstance": isinstance,
    "print": print,
    "True": True,
    "False": False,
    "None": None,
}


# ---------------------------------------------------------------------------
# AST validation
# ---------------------------------------------------------------------------

def validate_code(source: str) -> list[str]:
    """Parse and AST-walk source. Returns list of violation messages. Empty = valid."""
    violations: list[str] = []

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        violations.append(f"Syntax error: {exc}")
        return violations

    for node in ast.walk(tree):
        # Block import, global, nonlocal
        if isinstance(node, BLOCKED_NODE_TYPES):
            violations.append(
                f"Blocked statement: {type(node).__name__} (line {getattr(node, 'lineno', '?')})"
            )

        # Block calls to dangerous functions
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in BLOCKED_FUNCTION_NAMES:
                violations.append(
                    f"Blocked function call: {node.func.id} (line {node.lineno})"
                )

        # Block dangerous attribute access
        if isinstance(node, ast.Attribute):
            if node.attr in BLOCKED_ATTRIBUTE_NAMES:
                violations.append(
                    f"Blocked attribute access: .{node.attr} (line {node.lineno})"
                )

        # Block string-based attribute lookups that could bypass checks
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ("getattr", "setattr", "delattr"):
                violations.append(
                    f"Blocked dynamic attribute access: {node.func.id} (line {node.lineno})"
                )

    # Verify the code defines an async def run function
    has_run = any(
        isinstance(node, ast.AsyncFunctionDef) and node.name == "run"
        for node in ast.walk(tree)
    )
    if not has_run:
        violations.append("Code must define 'async def run(user_id, params, call_tool)'")

    return violations


# ---------------------------------------------------------------------------
# Sandboxed execution
# ---------------------------------------------------------------------------

async def execute_sandboxed(
    source: str,
    user_id: str,
    params: dict,
    call_tool: Callable | None = None,
    timeout_seconds: float = 30.0,
) -> str:
    """Validate, then exec code in restricted environment. Returns result string."""
    violations = validate_code(source)
    if violations:
        raise SandboxError(f"Code validation failed: {'; '.join(violations)}")

    # Build restricted globals
    restricted_globals: dict[str, Any] = {"__builtins__": SAFE_BUILTINS.copy()}

    # Execute the code to define the run function
    try:
        exec(source, restricted_globals)  # noqa: S102
    except Exception as exc:
        raise SandboxError(f"Code definition failed: {exc}") from exc

    run_fn = restricted_globals.get("run")
    if not callable(run_fn):
        raise SandboxError("Code must define a callable 'run' function")

    # Call the run function with timeout
    async def _noop_call_tool(name: str, arguments: dict) -> str:
        return f"Tool '{name}' not available in sandbox"

    tool_fn = call_tool or _noop_call_tool

    try:
        result = await asyncio.wait_for(
            run_fn(user_id, params, tool_fn),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise SandboxError(f"Execution timed out after {timeout_seconds}s")
    except SandboxError:
        raise
    except Exception as exc:
        raise SandboxError(f"Execution error: {exc}") from exc

    return str(result) if result is not None else "Done"


# ---------------------------------------------------------------------------
# CodeSkill class
# ---------------------------------------------------------------------------

class CodeSkill(BaseSkill):
    """A user-created code skill that runs in the sandbox."""

    def __init__(
        self,
        skill_name: str,
        skill_description: str,
        code: str,
        params_schema: dict | None = None,
    ) -> None:
        self._name = skill_name
        self._description = skill_description
        self._code = code
        self._params_schema = params_schema or {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "Input for the skill"},
            },
        }

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def category(self) -> str:
        return "custom"

    @property
    def parameters_schema(self) -> dict:
        return self._params_schema

    async def execute(self, user_id: str, params: dict) -> str:
        """Validate and execute the sandboxed code."""
        try:
            return await execute_sandboxed(
                source=self._code,
                user_id=user_id,
                params=params,
            )
        except SandboxError as exc:
            return f"Skill execution failed: {exc}"
