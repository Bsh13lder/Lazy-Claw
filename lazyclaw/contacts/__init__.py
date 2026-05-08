"""Unified contact store — one row per real person, multiple channel handles."""

from lazyclaw.contacts.store import (
    ContactRecord,
    create_contact,
    delete_contact,
    find_contact,
    get_contact,
    list_contacts,
    update_contact,
    upsert_from_macos,
)

__all__ = [
    "ContactRecord",
    "create_contact",
    "delete_contact",
    "find_contact",
    "get_contact",
    "list_contacts",
    "update_contact",
    "upsert_from_macos",
]
