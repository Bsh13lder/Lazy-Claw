"""AI-generated code skills via LLM."""

from __future__ import annotations

import json
import logging
import re

from lazyclaw.config import Config
from lazyclaw.llm.providers.base import LLMMessage
from lazyclaw.llm.router import LLMRouter
from lazyclaw.skills.sandbox import SandboxError, validate_code

logger = logging.getLogger(__name__)

GENERATION_PROMPT = """\
You are a code skill generator for LazyClaw, an AI agent platform.

Generate a Python code skill based on the user's description. Output ONLY valid JSON with these fields:
- "name": snake_case skill name (e.g., "summarize_text")
- "description": one-line description for the tool catalog
- "code": Python code that defines `async def run(user_id, params, call_tool)`
- "parameters_schema": JSON Schema for the params dict

Rules for the code:
1. Must define `async def run(user_id, params, call_tool)` as the entry point
2. NO imports allowed — use only builtins (len, range, str, int, float, list, dict, set, etc.)
3. NO exec, eval, compile, open, __import__, or attribute access to __class__, __globals__, etc.
4. The `call_tool` callback lets you invoke other skills: `result = await call_tool("skill_name", {"param": "value"})`
5. Must return a string result
6. Keep it simple and focused

Example output:
```json
{
  "name": "word_counter",
  "description": "Count words in the given text",
  "code": "async def run(user_id, params, call_tool):\\n    text = params.get('text', '')\\n    words = text.split()\\n    return f'Word count: {len(words)}'",
  "parameters_schema": {
    "type": "object",
    "properties": {
      "text": {"type": "string", "description": "Text to count words in"}
    },
    "required": ["text"]
  }
}
```
"""


def _parse_llm_response(content: str) -> dict:
    """Extract JSON from LLM output, handling markdown code blocks."""
    # Try to find JSON in code blocks first
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
    if json_match:
        content = json_match.group(1).strip()

    # Try direct JSON parse
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text
    brace_match = re.search(r"\{.*\}", content, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError("Could not parse JSON from LLM response")


async def generate_code_skill(
    config: Config,
    user_id: str,
    description: str,
    name: str | None = None,
) -> dict:
    """Use LLM to generate a code skill from description.

    Returns dict with id, name, code, description, parameters_schema.
    Raises SandboxError if generated code fails validation after retry.
    """
    router = LLMRouter(config)

    user_prompt = f"Create a code skill that: {description}"
    if name:
        user_prompt += f"\nUse the name: {name}"

    messages = [
        LLMMessage(role="system", content=GENERATION_PROMPT),
        LLMMessage(role="user", content=user_prompt),
    ]

    # First attempt
    response = await router.chat(config.brain_model, messages, user_id=user_id)
    parsed = _parse_llm_response(response.content)

    skill_name = name or parsed.get("name", "generated_skill")
    skill_code = parsed.get("code", "")
    skill_description = parsed.get("description", description)
    params_schema = parsed.get("parameters_schema", {"type": "object", "properties": {}})

    # Validate the generated code
    violations = validate_code(skill_code)

    if violations:
        # Retry once with error feedback
        retry_prompt = (
            f"The generated code has validation errors:\n"
            f"{chr(10).join(violations)}\n\n"
            f"Fix the code and output valid JSON again. Remember: no imports, "
            f"no exec/eval, must define 'async def run(user_id, params, call_tool)'."
        )
        messages.append(LLMMessage(role="assistant", content=response.content))
        messages.append(LLMMessage(role="user", content=retry_prompt))

        response = await router.chat(config.brain_model, messages, user_id=user_id)
        parsed = _parse_llm_response(response.content)

        skill_code = parsed.get("code", skill_code)
        skill_name = name or parsed.get("name", skill_name)
        skill_description = parsed.get("description", skill_description)
        params_schema = parsed.get("parameters_schema", params_schema)

        violations = validate_code(skill_code)
        if violations:
            raise SandboxError(f"Generated code still invalid: {'; '.join(violations)}")

    # Store the skill
    from lazyclaw.skills.manager import create_code_skill

    skill_id = await create_code_skill(
        config=config,
        user_id=user_id,
        name=skill_name,
        description=skill_description,
        code=skill_code,
        parameters_schema=params_schema,
    )

    logger.info("Generated code skill '%s' for user %s", skill_name, user_id)

    return {
        "id": skill_id,
        "name": skill_name,
        "description": skill_description,
        "code": skill_code,
        "parameters_schema": params_schema,
    }
