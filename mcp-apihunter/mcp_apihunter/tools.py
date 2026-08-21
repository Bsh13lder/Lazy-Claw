"""The panel_* operations, independent of the MCP wire layer.

`server.py` binds these methods to MCP tools; tests call them directly. Each
method takes a plain dict of arguments and returns a JSON-serializable dict —
no MCP types leak in here.
"""

from __future__ import annotations

from typing import Any

from mcp_apihunter.caller import MissingArguments, execute
from mcp_apihunter.discovery import endpoints_from_capture
from mcp_apihunter.manifest import CsrfSpec, Endpoint, Manifest, ManifestStore
from mcp_apihunter.probe import probe_official_api
from mcp_apihunter.session import (
    SessionProvider,
    SessionUnavailable,
    cookie_domain_for,
)


def _endpoint_from_args(args: dict[str, Any]) -> Endpoint:
    csrf = CsrfSpec(
        source=args.get("csrf_source", "none"),
        cookie_name=args.get("csrf_cookie_name", "csrftoken"),
        target=args.get("csrf_target", "header"),
        target_name=args.get("csrf_target_name", "X-CSRFToken"),
    )
    return Endpoint(
        name=args["name"],
        description=args.get("description", ""),
        method=args["method"],
        url_template=args["url_template"],
        query=args.get("query") or {},
        headers=args.get("headers") or {},
        body_type=args.get("body_type", "none"),
        body_template=args.get("body_template") or {},
        csrf=csrf,
        mutating=bool(args.get("mutating", False)),
        success_status=args.get("success_status") or [200, 201, 202, 204],
        response_hint=args.get("response_hint", ""),
    )


class PanelTools:
    """Stateless-per-call operations over a ManifestStore + SessionProvider."""

    def __init__(self, store: ManifestStore, provider: SessionProvider) -> None:
        self._store = store
        self._provider = provider

    async def learn(self, args: dict[str, Any]) -> dict[str, Any]:
        site = args["site"]
        manifest = self._store.load(site)
        if manifest is None:
            base_url = args.get("base_url")
            if not base_url:
                return {"error": f"site '{site}' is new — base_url is required to create it"}
            manifest = Manifest(
                site=site,
                base_url=base_url,
                cookie_domain=args.get("cookie_domain", ""),
                login_url=args.get("login_url", ""),
            )
        endpoint = _endpoint_from_args(args)
        manifest = manifest.with_endpoint(endpoint)
        self._store.save(manifest)
        return {
            "status": "saved",
            "site": site,
            "endpoint": endpoint.name,
            "mutating": endpoint.mutating,
            "total_endpoints": len(manifest.endpoints),
        }

    async def list_sites(self, args: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"sites": self._store.list_sites()}

    async def list_endpoints(self, args: dict[str, Any]) -> dict[str, Any]:
        manifest = self._store.load(args["site"])
        if manifest is None:
            return {"error": f"unknown site '{args['site']}'"}
        return {
            "site": manifest.site,
            "base_url": manifest.base_url,
            "endpoints": [
                {
                    "name": ep.name,
                    "method": ep.method,
                    "mutating": ep.mutating,
                    "description": ep.description,
                }
                for ep in manifest.endpoints
            ],
        }

    async def describe(self, args: dict[str, Any]) -> dict[str, Any]:
        manifest = self._store.load(args["site"])
        if manifest is None:
            return {"error": f"unknown site '{args['site']}'"}
        endpoint = manifest.get(args["name"])
        if endpoint is None:
            return {"error": f"unknown endpoint '{args['name']}' for site '{args['site']}'"}
        return endpoint.model_dump()

    async def call(self, args: dict[str, Any]) -> dict[str, Any]:
        manifest = self._store.load(args["site"])
        if manifest is None:
            return {"error": f"unknown site '{args['site']}'"}
        endpoint = manifest.get(args["name"])
        if endpoint is None:
            return {"error": f"unknown endpoint '{args['name']}' for site '{args['site']}'"}
        try:
            outcome = await execute(
                manifest,
                endpoint,
                args.get("args") or {},
                self._provider,
                confirm=bool(args.get("confirm", False)),
            )
        except MissingArguments as exc:
            return {"error": str(exc), "missing": exc.names}
        return outcome.to_dict()

    async def forget(self, args: dict[str, Any]) -> dict[str, Any]:
        site = args["site"]
        name = args.get("name")
        if name:
            manifest = self._store.load(site)
            if manifest is None:
                return {"error": f"unknown site '{site}'"}
            self._store.save(manifest.without_endpoint(name))
            return {"status": "removed_endpoint", "site": site, "endpoint": name}
        removed = self._store.delete(site)
        return {"status": "removed_site" if removed else "not_found", "site": site}

    async def learn_from_capture(self, args: dict[str, Any]) -> dict[str, Any]:
        """Bulk-ingest a browser network capture into endpoint skeletons."""
        site = args["site"]
        records = args.get("records") or []
        manifest = self._store.load(site)
        if manifest is None:
            base_url = args.get("base_url")
            if not base_url:
                return {"error": f"site '{site}' is new — base_url is required to create it"}
            manifest = Manifest(
                site=site,
                base_url=base_url,
                cookie_domain=args.get("cookie_domain", ""),
                login_url=args.get("login_url", ""),
            )
        skeletons = endpoints_from_capture(records, manifest.base_url)
        for ep in skeletons:
            manifest = manifest.with_endpoint(ep)
        self._store.save(manifest)
        return {
            "status": "learned",
            "site": site,
            "learned": [{"name": e.name, "method": e.method,
                         "url_template": e.url_template, "mutating": e.mutating}
                        for e in skeletons],
            "count": len(skeletons),
            "note": "skeletons only — verify body/params/CSRF with panel_learn_endpoint, "
                    "then confirm with panel_call.",
        }

    async def probe_api(self, args: dict[str, Any]) -> dict[str, Any]:
        """Check a panel for an official API (OpenAPI / WordPress / GraphQL)."""
        site = args.get("site")
        base_url = args.get("base_url")
        cookie_domain = args.get("cookie_domain", "")
        if site:
            manifest = self._store.load(site)
            if manifest is None:
                return {"error": f"unknown site '{site}'"}
            base_url = manifest.base_url
            cookie_domain = manifest.cookie_domain or cookie_domain_for(base_url)
        if not base_url:
            return {"error": "provide either a known 'site' or a 'base_url' to probe"}
        session = None
        try:
            session = await self._provider.resolve(
                cookie_domain or cookie_domain_for(base_url)
            )
        except SessionUnavailable:
            session = None  # probe unauthenticated — many schema endpoints are public
        result = await probe_official_api(base_url, session)
        return result.to_dict()

    async def discover(self, args: dict[str, Any]) -> dict[str, Any]:
        site = args["site"]
        base_url = args["base_url"]
        manifest = self._store.load(site)
        if manifest is None:
            manifest = Manifest(
                site=site,
                base_url=base_url,
                cookie_domain=args.get("cookie_domain", "") or cookie_domain_for(base_url),
                login_url=args.get("login_url", ""),
            )
            self._store.save(manifest)
        return {
            "status": "recording",
            "site": site,
            "cookie_domain": manifest.cookie_domain,
            "guidance": (
                "Log in to the panel in the browser, then perform each target "
                "task once (e.g. post a blog, open analytics). For every "
                "backend XHR/fetch you observe, call panel_learn_endpoint with "
                "its method, url_template, body_template and CSRF source. Mark "
                "any write/delete/publish call mutating=true."
            ),
        }
