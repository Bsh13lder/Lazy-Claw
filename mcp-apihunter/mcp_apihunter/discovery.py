"""Turn captured browser traffic into endpoint skeletons.

The browser's network capture reports method + URL + status + content-type for
each request, but NOT request headers or bodies. So we can't fully author an
endpoint from a capture — but we CAN discover *which* endpoints exist and
parameterize their URLs, which is the tedious part. The agent then fills in the
body/CSRF details with `panel_learn_endpoint`.

`endpoints_from_capture` filters a capture down to the panel's own backend
calls (dropping static assets, third-party hosts, analytics, failures) and
emits one `Endpoint` skeleton per distinct method+path.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from mcp_apihunter.manifest import CsrfSpec, Endpoint

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Content types that are page chrome / assets, never a data endpoint.
_ASSET_MIME_PREFIXES = ("text/css", "image/", "font/", "video/", "audio/")
_ASSET_MIME_EXACT = frozenset({
    "application/javascript", "text/javascript", "application/font-woff",
    "application/font-woff2", "text/html",
})
_ASSET_PATH_RE = re.compile(
    r"\.(?:css|js|mjs|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|eot|map|mp4|webm)(?:$|\?)",
    re.IGNORECASE,
)
# Hosts that are analytics / tag managers, never the panel's own API.
_ANALYTICS_HOSTS = (
    "google-analytics.com", "googletagmanager.com", "doubleclick.net",
    "segment.io", "segment.com", "sentry.io", "hotjar.com", "mixpanel.com",
    "facebook.com", "fbcdn.net", "cloudflareinsights.com", "posthog.com",
)

# Path segments that are almost certainly identifiers to parameterize.
_NUMERIC_SEG = re.compile(r"^\d+$")
_UUID_SEG = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_LONG_HEX_SEG = re.compile(r"^[0-9a-f]{16,}$", re.I)
_SLUG_NAME_RE = re.compile(r"[^a-z0-9]+")


def _is_asset(url: str, mime: str | None) -> bool:
    if mime:
        m = mime.split(";", 1)[0].strip().lower()
        if m in _ASSET_MIME_EXACT or m.startswith(_ASSET_MIME_PREFIXES):
            return True
    return bool(_ASSET_PATH_RE.search(urlparse(url).path))


def _same_site(url: str, base_host: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    if any(host.endswith(a) for a in _ANALYTICS_HOSTS):
        return False
    base = base_host.lstrip(".").lower()
    return host == base or host.endswith("." + base) or base.endswith("." + host)


def _parameterize_path(path: str) -> tuple[str, list[str]]:
    """Replace id-like segments with {id}, {id2}, … Returns (template, names)."""
    parts = path.split("/")
    names: list[str] = []
    out: list[str] = []
    for seg in parts:
        if _NUMERIC_SEG.match(seg) or _UUID_SEG.match(seg) or _LONG_HEX_SEG.match(seg):
            name = "id" if not names else f"id{len(names) + 1}"
            names.append(name)
            out.append("{" + name + "}")
        else:
            out.append(seg)
    return "/".join(out), names


def _endpoint_name(method: str, template: str) -> str:
    """A readable slug from method + the last meaningful path segments."""
    segs = [s for s in template.split("/") if s and not s.startswith("{")]
    tail = "_".join(segs[-2:]) if segs else "root"
    tail = _SLUG_NAME_RE.sub("_", tail.lower()).strip("_") or "root"
    prefix = {"GET": "get", "POST": "create", "PUT": "update",
              "PATCH": "update", "DELETE": "delete"}.get(method, method.lower())
    return f"{prefix}_{tail}"[:64]


def endpoints_from_capture(
    records: list[dict[str, Any]],
    base_url: str,
    *,
    csrf_cookie: str = "csrftoken",
) -> list[Endpoint]:
    """Build endpoint skeletons from a browser network capture.

    `records` are the rows from the browser `network` action (dicts with
    ``url``, ``method``, ``status``, ``mime_type``, ``failed``, ``from_cache``).
    Static assets, third-party/analytics hosts, failures and redirects are
    dropped. Mutating requests get a cookie-CSRF default (the common Django/SPA
    admin pattern) and ``mutating=true``; the agent corrects specifics later.
    """
    base_host = urlparse(base_url).hostname or ""
    seen: set[tuple[str, str]] = set()
    endpoints: list[Endpoint] = []

    for rec in records:
        url = rec.get("url") or ""
        method = (rec.get("method") or "GET").upper()
        if rec.get("failed") or not url:
            continue
        status = rec.get("status")
        if status is not None and (status < 200 or status >= 300):
            continue  # only 2xx: a 3xx redirect on an XHR is an auth bounce
        if not _same_site(url, base_host):
            continue
        if _is_asset(url, rec.get("mime_type")):
            continue

        path = urlparse(url).path or "/"
        template, _ = _parameterize_path(path)
        key = (method, template)
        if key in seen:
            continue
        seen.add(key)

        mutating = method in _MUTATING_METHODS
        csrf = (
            CsrfSpec(source="cookie", cookie_name=csrf_cookie,
                     target="header", target_name="X-CSRFToken")
            if mutating else CsrfSpec()
        )
        endpoints.append(
            Endpoint(
                name=_endpoint_name(method, template),
                description=f"auto-discovered from {method} {path}",
                method=method,
                url_template=template,
                body_type="json" if mutating else "none",
                csrf=csrf,
                mutating=mutating,
                response_hint="skeleton from network capture — verify body/params",
            )
        )
    return endpoints
