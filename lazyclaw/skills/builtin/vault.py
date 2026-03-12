from __future__ import annotations
from lazyclaw.skills.base import BaseSkill


class VaultSetSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "vault_set"

    @property
    def description(self) -> str:
        return (
            "Securely store a credential (API key, token, password) in the encrypted vault. "
            "The value is encrypted with AES-256-GCM before storage. "
            "Common keys: openai_api_key, anthropic_api_key, github_token"
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Credential name (e.g., 'openai_api_key', 'github_token')",
                },
                "value": {
                    "type": "string",
                    "description": "The secret value to store",
                },
            },
            "required": ["key", "value"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Vault not configured"
        from lazyclaw.crypto.vault import set_credential
        await set_credential(self._config, user_id, params["key"], params["value"])
        return f"Credential '{params['key']}' saved securely."


class VaultListSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "vault_list"

    @property
    def description(self) -> str:
        return "List all stored credential names in the encrypted vault. Shows key names only, never values."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Vault not configured"
        from lazyclaw.crypto.vault import list_credentials
        keys = await list_credentials(self._config, user_id)
        if not keys:
            return "No credentials stored in vault."
        return "Stored credentials:\n" + "\n".join(f"- {k}" for k in keys)


class VaultDeleteSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "vault_delete"

    @property
    def description(self) -> str:
        return "Delete a credential from the encrypted vault."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Credential name to delete",
                },
            },
            "required": ["key"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Vault not configured"
        from lazyclaw.crypto.vault import delete_credential
        deleted = await delete_credential(self._config, user_id, params["key"])
        if deleted:
            return f"Credential '{params['key']}' deleted."
        return f"No credential found with key '{params['key']}'."
