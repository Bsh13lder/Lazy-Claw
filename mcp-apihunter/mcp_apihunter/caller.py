"""Replay a discovered endpoint as a real HTTP call.

Given an `Endpoint` from the manifest, the arguments the agent supplied, and a
`SessionProvider` for auth, `execute` renders the request (URL/query/headers/
body with `{placeholder}` substitution), injects CSRF + cookies, sends it, and
classifies the response into a small set of outcomes the agent can act on:

    ok             — succeeded; ``body`` holds the parsed response
    needs_confirm  — a mutating call was attempted without confirm=True
    needs_relogin  — the session expired / isn't authenticated
    error          — the endpoint returned an unexpected status

Nothing here blocks on a human; the confirm gate simply refuses mutating calls
until the agent (which owns the user relationship) passes ``confirm=True``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from mcp_apihunter.manifest import Endpoint, Manifest
from mcp_apihunter.session import SessionProvider, SessionUnavailable, cookie_domain_for

_PLACEHOLDER_RE = re.compile(r"\{([a-z0-9_]+)\}")
_FULL_PLACEHOLDER_RE = re.compile(r"^\{([a-z0-9_]+)\}$")
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_BODY_CHARS = 20_000


@dataclass(frozen=True)
class CallOutcome:
    status: str  # ok | needs_confirm | needs_relogin | error
    http_status: int | None = None
    body: Any = None
    message: str = ""
    warning: str = ""  # non-fatal, e.g. a possible-stale signal

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status}
        if self.http_status is not None:
            out["http_status"] = self.http_status
        if self.message:
            out["message"] = self.message
        if self.warning:
            out["warning"] = self.warning
        if self.body is not None:
            out["body"] = self.body
        return out


class MissingArguments(ValueError):
    """Raised when a template references args the caller didn't supply."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        super().__init__(f"missing required arguments: {', '.join(sorted(set(names)))}")


def _render(value: Any, args: dict[str, Any], missing: list[str]) -> Any:
    """Substitute ``{name}`` placeholders from ``args``, recursively.

    A string that is exactly one placeholder yields the raw arg value (so a
    JSON body keeps ints/bools/lists); any other string does textual
    interpolation. Unknown placeholders are recorded in ``missing``.
    """
    if isinstance(value, str):
        full = _FULL_PLACEHOLDER_RE.match(value)
        if full:
            key = full.group(1)
            if key not in args:
                missing.append(key)
                return None
            return args[key]

        def _sub(match: re.Match) -> str:
            key = match.group(1)
            if key not in args:
                missing.append(key)
                return ""
            return str(args[key])

        return _PLACEHOLDER_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _render(v, args, missing) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, args, missing) for v in value]
    return value


def _apply_csrf(
    endpoint: Endpoint,
    cookies: dict[str, str],
    headers: dict[str, str],
    body: Any,
    params: dict[str, Any],
) -> str | None:
    """Place the CSRF token per the endpoint's spec. Returns the token used."""
    spec = endpoint.csrf
    if spec.source == "none":
        return None
    token = cookies.get(spec.cookie_name)
    if not token:
        return None
    if spec.target == "header":
        headers[spec.target_name] = token
    elif spec.target == "query":
        params[spec.target_name] = token
    elif spec.target == "form" and isinstance(body, dict):
        body[spec.target_name] = token
    return token


def _looks_like_login_redirect(resp: httpx.Response) -> bool:
    location = resp.headers.get("location", "").lower()
    return any(k in location for k in ("login", "signin", "sign-in", "auth"))


def _is_api_shaped(endpoint: Endpoint) -> bool:
    """True if this endpoint is expected to return structured data, not a page."""
    return (
        endpoint.body_type == "json"
        or "/api" in endpoint.url_template
        or endpoint.csrf.source != "none"
    )


def _stale_warning(endpoint: Endpoint, resp: httpx.Response) -> str:
    """Flag a likely schema-drift / bounced-session response.

    An API-shaped endpoint that returns HTML on a 2xx usually means the panel
    changed or the request silently landed on a page — worth re-discovering.
    """
    ctype = resp.headers.get("content-type", "").lower()
    if _is_api_shaped(endpoint) and "html" in ctype and "json" not in ctype:
        return (
            "expected structured data but got HTML — this endpoint may be "
            "stale (panel changed) or the session bounced; consider "
            "re-recording it with panel_discover."
        )
    return ""


def _parse_body(resp: httpx.Response) -> Any:
    ctype = resp.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            return resp.json()
        except ValueError:
            pass
    text = resp.text
    if len(text) > _MAX_BODY_CHARS:
        return text[:_MAX_BODY_CHARS] + f"\n…[truncated {len(text) - _MAX_BODY_CHARS} chars]"
    return text


async def execute(
    manifest: Manifest,
    endpoint: Endpoint,
    args: dict[str, Any],
    provider: SessionProvider,
    *,
    confirm: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 30.0,
) -> CallOutcome:
    """Render, authenticate, send, and classify one endpoint call."""
    if endpoint.mutating and not confirm:
        return CallOutcome(
            status="needs_confirm",
            message=(
                f"'{endpoint.name}' is a state-changing call "
                f"({endpoint.method} {endpoint.url_template}). Re-invoke with "
                "confirm=true once the user has approved it."
            ),
        )

    missing: list[str] = []
    path = _render(endpoint.url_template, args, missing)
    params = _render(dict(endpoint.query), args, missing)
    headers = _render(dict(endpoint.headers), args, missing)
    body: Any = None
    if endpoint.body_type != "none":
        body = _render(dict(endpoint.body_template), args, missing)
    if missing:
        raise MissingArguments(missing)

    domain = manifest.cookie_domain or cookie_domain_for(manifest.base_url)
    try:
        session = await provider.resolve(domain)
    except SessionUnavailable as exc:
        return CallOutcome(
            status="needs_relogin",
            message=(
                f"{exc}. Log in to {manifest.login_url or manifest.base_url} "
                "in the browser, then retry."
            ),
        )

    _apply_csrf(endpoint, session.cookies, headers, body, params)

    url = path if path.startswith(("http://", "https://")) else manifest.base_url + path

    request_kwargs: dict[str, Any] = {"params": params, "headers": headers}
    if endpoint.body_type == "json":
        request_kwargs["json"] = body
    elif endpoint.body_type == "form":
        request_kwargs["data"] = body

    async with httpx.AsyncClient(
        transport=transport,
        cookies=session.cookies,
        timeout=timeout,
        follow_redirects=False,
    ) as client:
        try:
            resp = await client.request(endpoint.method, url, **request_kwargs)
        except httpx.HTTPError as exc:
            return CallOutcome(status="error", message=f"request failed: {exc}")

    if resp.status_code in (401, 403):
        return CallOutcome(
            status="needs_relogin",
            http_status=resp.status_code,
            message="server rejected the session (auth expired) — re-login and retry",
        )
    if resp.status_code in _REDIRECT_STATUSES and _looks_like_login_redirect(resp):
        return CallOutcome(
            status="needs_relogin",
            http_status=resp.status_code,
            message="request was redirected to a login page — re-login and retry",
        )
    if resp.status_code in endpoint.success_status:
        return CallOutcome(
            status="ok",
            http_status=resp.status_code,
            body=_parse_body(resp),
            warning=_stale_warning(endpoint, resp),
        )
    return CallOutcome(
        status="error",
        http_status=resp.status_code,
        body=_parse_body(resp),
        message=f"unexpected status {resp.status_code} (expected {endpoint.success_status})",
    )
