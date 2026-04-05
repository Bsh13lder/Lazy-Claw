"""Pipeline skills — generic CRM/deals CRUD with encrypted storage.

Stages are user-defined strings (not hardcoded). Users create custom
workflow skills on top of these primitives via the Web UI or Telegram.
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


class PipelineAddContactSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "pipeline_add_contact"

    @property
    def description(self) -> str:
        return (
            "Add a contact to the pipeline (customer, lead, partner). "
            "All personal data is encrypted."
        )

    @property
    def category(self) -> str:
        return "pipeline"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Full name"},
                "phone": {"type": "string", "description": "Phone number"},
                "email": {"type": "string", "description": "Email address"},
                "notes": {"type": "string", "description": "Free-text notes"},
                "stage": {
                    "type": "string",
                    "description": "Pipeline stage (any string, e.g. 'new', 'lead', 'active', 'vip')",
                    "default": "new",
                },
                "tags": {"type": "string", "description": "Comma-separated tags"},
            },
            "required": ["name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.pipeline.store import create_contact

        name = params.get("name", "").strip()
        if not name:
            return "Name is required."

        try:
            contact = await create_contact(
                self._config, user_id,
                name=name,
                phone=params.get("phone"),
                email=params.get("email"),
                notes=params.get("notes"),
                stage=params.get("stage", "new"),
                tags=params.get("tags"),
            )
            parts = [f"Contact added: {name}", f"ID: {contact['id']}"]
            if contact.get("phone"):
                parts.append(f"Phone: {contact['phone']}")
            if contact.get("stage"):
                parts.append(f"Stage: {contact['stage']}")
            return "\n".join(parts)
        except Exception as exc:
            logger.error("Failed to create contact: %s", exc)
            return f"Error: {exc}"


class PipelineListContactsSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "pipeline_list_contacts"

    @property
    def description(self) -> str:
        return "List pipeline contacts. Filter by stage or search by name/phone/email."

    @property
    def category(self) -> str:
        return "pipeline"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "stage": {"type": "string", "description": "Filter by stage"},
                "search": {"type": "string", "description": "Search name/phone/email/notes"},
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.pipeline.store import list_contacts

        try:
            contacts = await list_contacts(
                self._config, user_id,
                stage=params.get("stage"),
                search=params.get("search"),
            )
        except Exception as exc:
            logger.error("Failed to list contacts: %s", exc)
            return f"Error: {exc}"

        if not contacts:
            return "No contacts found."

        lines: list[str] = [f"Contacts ({len(contacts)}):"]
        for c in contacts:
            parts = [f"- {c['name']}"]
            if c.get("phone"):
                parts[0] += f" | {c['phone']}"
            if c.get("stage"):
                parts[0] += f" [{c['stage']}]"
            if c.get("tags"):
                parts[0] += f" #{c['tags']}"
            parts.append(f"  ID: {c['id']}")
            lines.extend(parts)
        return "\n".join(lines)


class PipelineUpdateContactSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "pipeline_update_contact"

    @property
    def description(self) -> str:
        return "Update a contact's fields (name, phone, email, notes, stage, tags)."

    @property
    def category(self) -> str:
        return "pipeline"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "Contact ID"},
                "name": {"type": "string", "description": "Updated name"},
                "phone": {"type": "string", "description": "Updated phone"},
                "email": {"type": "string", "description": "Updated email"},
                "notes": {"type": "string", "description": "Updated notes"},
                "stage": {"type": "string", "description": "Move to stage"},
                "tags": {"type": "string", "description": "Updated tags"},
            },
            "required": ["contact_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.pipeline.store import update_contact

        contact_id = params.pop("contact_id", "")
        if not contact_id:
            return "contact_id is required."

        fields = {k: v for k, v in params.items() if v is not None}
        if not fields:
            return "No fields to update."

        try:
            ok = await update_contact(self._config, user_id, contact_id, **fields)
            if ok:
                updated = ", ".join(f"{k}={v}" for k, v in fields.items())
                return f"Contact updated: {updated}"
            return "Contact not found."
        except Exception as exc:
            logger.error("Failed to update contact: %s", exc)
            return f"Error: {exc}"


class PipelineDeleteContactSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "pipeline_delete_contact"

    @property
    def description(self) -> str:
        return "Delete a contact and all their deals."

    @property
    def category(self) -> str:
        return "pipeline"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "Contact ID to delete"},
            },
            "required": ["contact_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.pipeline.store import delete_contact

        contact_id = params.get("contact_id", "")
        if not contact_id:
            return "contact_id is required."

        try:
            ok = await delete_contact(self._config, user_id, contact_id)
            return "Contact deleted." if ok else "Contact not found."
        except Exception as exc:
            logger.error("Failed to delete contact: %s", exc)
            return f"Error: {exc}"


# ---------------------------------------------------------------------------
# Deals
# ---------------------------------------------------------------------------


class PipelineAddDealSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "pipeline_add_deal"

    @property
    def description(self) -> str:
        return (
            "Create a deal/transaction linked to a contact. "
            "Track amount, currency, stage, and extra data."
        )

    @property
    def category(self) -> str:
        return "pipeline"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "Contact ID to link"},
                "title": {"type": "string", "description": "Deal title/description"},
                "amount": {"type": "number", "description": "Deal amount"},
                "currency": {
                    "type": "string",
                    "description": "Currency code (EUR, USD, GEL, etc.)",
                    "default": "EUR",
                },
                "stage": {
                    "type": "string",
                    "description": "Deal stage (any string, e.g. 'inquiry', 'quoted', 'payment_pending', 'paid', 'fulfilled')",
                    "default": "inquiry",
                },
                "description": {"type": "string", "description": "Detailed description"},
                "data": {"type": "string", "description": "Extra data (JSON or free text — encrypted)"},
            },
            "required": ["contact_id", "title"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.pipeline.store import create_deal

        contact_id = params.get("contact_id", "")
        title = params.get("title", "").strip()
        if not contact_id:
            return "contact_id is required."
        if not title:
            return "title is required."

        try:
            deal = await create_deal(
                self._config, user_id,
                contact_id=contact_id,
                title=title,
                description=params.get("description"),
                amount=params.get("amount", 0),
                currency=params.get("currency", "EUR"),
                stage=params.get("stage", "inquiry"),
                data=params.get("data"),
            )
            return (
                f"Deal created: {title}\n"
                f"ID: {deal['id']}\n"
                f"Amount: {deal['amount']} {deal['currency']}\n"
                f"Stage: {deal['stage']}"
            )
        except Exception as exc:
            logger.error("Failed to create deal: %s", exc)
            return f"Error: {exc}"


class PipelineListDealsSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "pipeline_list_deals"

    @property
    def description(self) -> str:
        return "List deals in the pipeline. Filter by contact or stage."

    @property
    def category(self) -> str:
        return "pipeline"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "Filter by contact"},
                "stage": {"type": "string", "description": "Filter by stage"},
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.pipeline.store import list_deals

        try:
            deals = await list_deals(
                self._config, user_id,
                contact_id=params.get("contact_id"),
                stage=params.get("stage"),
            )
        except Exception as exc:
            logger.error("Failed to list deals: %s", exc)
            return f"Error: {exc}"

        if not deals:
            return "No deals found."

        lines: list[str] = [f"Deals ({len(deals)}):"]
        for d in deals:
            lines.append(
                f"- {d['title']} | {d['amount']} {d['currency']} [{d['stage']}]\n"
                f"  Contact: {d['contact_id']} | ID: {d['id']}"
            )
        return "\n".join(lines)


class PipelineUpdateDealSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "pipeline_update_deal"

    @property
    def description(self) -> str:
        return "Update a deal's fields (title, amount, stage, description, data)."

    @property
    def category(self) -> str:
        return "pipeline"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "deal_id": {"type": "string", "description": "Deal ID"},
                "title": {"type": "string", "description": "Updated title"},
                "amount": {"type": "number", "description": "Updated amount"},
                "currency": {"type": "string", "description": "Updated currency"},
                "stage": {"type": "string", "description": "Move to stage"},
                "description": {"type": "string", "description": "Updated description"},
                "data": {"type": "string", "description": "Updated extra data"},
            },
            "required": ["deal_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.pipeline.store import update_deal

        deal_id = params.pop("deal_id", "")
        if not deal_id:
            return "deal_id is required."

        fields = {k: v for k, v in params.items() if v is not None}
        if not fields:
            return "No fields to update."

        try:
            ok = await update_deal(self._config, user_id, deal_id, **fields)
            if ok:
                updated = ", ".join(f"{k}={v}" for k, v in fields.items())
                return f"Deal updated: {updated}"
            return "Deal not found."
        except Exception as exc:
            logger.error("Failed to update deal: %s", exc)
            return f"Error: {exc}"


class PipelineDeleteDealSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "pipeline_delete_deal"

    @property
    def description(self) -> str:
        return "Delete a deal from the pipeline."

    @property
    def category(self) -> str:
        return "pipeline"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "deal_id": {"type": "string", "description": "Deal ID to delete"},
            },
            "required": ["deal_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.pipeline.store import delete_deal

        deal_id = params.get("deal_id", "")
        if not deal_id:
            return "deal_id is required."

        try:
            ok = await delete_deal(self._config, user_id, deal_id)
            return "Deal deleted." if ok else "Deal not found."
        except Exception as exc:
            logger.error("Failed to delete deal: %s", exc)
            return f"Error: {exc}"
