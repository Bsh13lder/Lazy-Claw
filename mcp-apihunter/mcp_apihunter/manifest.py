"""Endpoint manifest model + encrypted per-site store.

A *manifest* is the durable record of one admin panel: its base URL, how to
re-login, and the set of discovered endpoints that `panel_call` can replay.
Manifests are validated at every boundary (Pydantic) and stored one file per
site, encrypted at rest.

All model mutations return NEW objects — nothing is edited in place — so a
manifest handed to a caller can never be changed underneath it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from mcp_apihunter import crypto

_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_SITE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class CsrfSpec(BaseModel, frozen=True):
    """Where a CSRF token comes from and where it must be placed.

    Phase 1 supports the ``cookie`` source (Django/most SPA admin panels echo
    the CSRF cookie back in a header). ``none`` means the endpoint needs no
    CSRF token. Page-scraped sources (meta tag / hidden input) are Phase 2.
    """

    source: Literal["none", "cookie"] = "none"
    cookie_name: str = "csrftoken"
    target: Literal["header", "form", "query"] = "header"
    target_name: str = "X-CSRFToken"


class Endpoint(BaseModel, frozen=True):
    """One replayable HTTP call within a site."""

    name: str
    description: str = ""
    method: str
    url_template: str
    query: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body_type: Literal["none", "json", "form"] = "none"
    body_template: dict[str, Any] = Field(default_factory=dict)
    csrf: CsrfSpec = Field(default_factory=CsrfSpec)
    mutating: bool = False
    success_status: list[int] = Field(default_factory=lambda: [200, 201, 202, 204])
    response_hint: str = ""

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                "endpoint name must be a slug: lowercase letters, digits, "
                "underscores (max 64 chars), e.g. 'post_blog'"
            )
        return v

    @field_validator("method")
    @classmethod
    def _valid_method(cls, v: str) -> str:
        upper = v.strip().upper()
        if upper not in _HTTP_METHODS:
            raise ValueError(
                f"method must be one of {sorted(_HTTP_METHODS)}, got {v!r}"
            )
        return upper

    @field_validator("url_template")
    @classmethod
    def _valid_url(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("url_template must not be empty")
        return v.strip()


class Manifest(BaseModel, frozen=True):
    """Everything apihunter knows about one admin panel."""

    site: str
    base_url: str
    cookie_domain: str = ""
    login_url: str = ""
    endpoints: list[Endpoint] = Field(default_factory=list)
    version: int = 1

    @field_validator("site")
    @classmethod
    def _valid_site(cls, v: str) -> str:
        if not _SITE_RE.match(v):
            raise ValueError(
                "site must be a slug: lowercase letters, digits, hyphens or "
                "underscores (max 64 chars), e.g. 'himap-admin'"
            )
        return v

    @field_validator("base_url")
    @classmethod
    def _valid_base_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("base_url must start with http:// or https://")
        return v

    def get(self, name: str) -> Endpoint | None:
        """Return the endpoint with ``name``, or None."""
        for ep in self.endpoints:
            if ep.name == name:
                return ep
        return None

    def with_endpoint(self, endpoint: Endpoint) -> "Manifest":
        """Return a NEW manifest with ``endpoint`` added or replaced by name."""
        kept = [ep for ep in self.endpoints if ep.name != endpoint.name]
        return self.model_copy(
            update={"endpoints": [*kept, endpoint], "version": self.version + 1}
        )

    def without_endpoint(self, name: str) -> "Manifest":
        """Return a NEW manifest with the named endpoint removed."""
        kept = [ep for ep in self.endpoints if ep.name != name]
        return self.model_copy(
            update={"endpoints": kept, "version": self.version + 1}
        )


class ManifestStore:
    """Encrypted file store: one ``{site}.json.enc`` per manifest.

    Files live under ``{root}/apihunter/``. ``root`` is normally the user's
    browser-profile dir (private, per-user), and the encryption key is derived
    from the server secret + user id — so a stolen file is useless without the
    server secret, and one user's key can't read another's manifest.
    """

    def __init__(self, root: Path, server_secret: str, user_id: str) -> None:
        self._dir = Path(root) / "apihunter"
        self._server_secret = server_secret
        self._user_id = user_id

    def _key(self) -> bytes:
        return crypto.derive_manifest_key(self._server_secret, self._user_id)

    def _path(self, site: str) -> Path:
        # site is slug-validated by the Manifest model; guard anyway so a raw
        # string from a tool arg can never escape the apihunter dir.
        if not _SITE_RE.match(site):
            raise ValueError(f"invalid site slug: {site!r}")
        return self._dir / f"{site}.json.enc"

    def list_sites(self) -> list[str]:
        if not self._dir.is_dir():
            return []
        return sorted(
            p.name[: -len(".json.enc")]
            for p in self._dir.glob("*.json.enc")
        )

    def load(self, site: str) -> Manifest | None:
        path = self._path(site)
        if not path.is_file():
            return None
        token = path.read_text(encoding="utf-8")
        raw = crypto.decrypt(token, self._key(), crypto.manifest_aad(self._user_id, site))
        return Manifest.model_validate_json(raw)

    def save(self, manifest: Manifest) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        token = crypto.encrypt(
            manifest.model_dump_json(),
            self._key(),
            crypto.manifest_aad(self._user_id, manifest.site),
        )
        path = self._path(manifest.site)
        # Write-then-rename so a crash mid-write can't corrupt an existing
        # manifest (the whole file is replaced atomically).
        tmp = path.with_suffix(".enc.tmp")
        tmp.write_text(token, encoding="utf-8")
        tmp.replace(path)

    def delete(self, site: str) -> bool:
        path = self._path(site)
        if path.is_file():
            path.unlink()
            return True
        return False
