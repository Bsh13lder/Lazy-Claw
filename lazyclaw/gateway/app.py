from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from lazyclaw.config import load_config
from lazyclaw.db.connection import init_db
from lazyclaw.gateway.auth import User, auth_router, get_current_user
from lazyclaw.gateway.routes.memory import router as memory_router
from lazyclaw.gateway.routes.skills import router as skills_router
from lazyclaw.gateway.routes.vault import router as vault_router
from lazyclaw.gateway.routes.browser import router as browser_router
from lazyclaw.gateway.routes.connector import router as connector_router
from lazyclaw.gateway.routes.connector import ws_router as connector_ws_router
from lazyclaw.gateway.routes.mcp import router as mcp_router
from lazyclaw.gateway.routes.jobs import router as jobs_router
from lazyclaw.gateway.routes.eco import router as eco_router
from lazyclaw.gateway.routes.permissions import router as permissions_router
from lazyclaw.gateway.routes.teams import router as teams_router
from lazyclaw.gateway.routes.compression import router as compression_router
from lazyclaw.gateway.routes.replay import router as replay_router
from lazyclaw.llm.model_manager import seed_default_models

logger = logging.getLogger(__name__)

_config = load_config()

# Shared state — set by cli.py at startup
_lane_queue = None
_shared_registry = None


def set_lane_queue(queue) -> None:
    """Called by cli.py to inject the shared LaneQueue."""
    global _lane_queue
    _lane_queue = queue


def set_registry(registry) -> None:
    """Called by cli.py to inject the shared SkillRegistry (with MCP tools)."""
    global _shared_registry
    _shared_registry = registry


@asynccontextmanager
async def lifespan(application: FastAPI):
    if not _config.server_secret or len(_config.server_secret) < 32:
        raise RuntimeError(
            "SERVER_SECRET must be set and at least 32 characters. "
            "Run 'lazyclaw setup' or set SERVER_SECRET in .env"
        )
    await init_db(_config)
    await seed_default_models(_config)
    logger.info("Database initialized, models seeded")
    yield


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response


app = FastAPI(title="LazyClaw", version="0.1.0", lifespan=lifespan)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_config.cors_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(auth_router)
app.include_router(skills_router)
app.include_router(memory_router)
app.include_router(vault_router)
app.include_router(browser_router)
app.include_router(connector_router)
app.include_router(connector_ws_router)
app.include_router(mcp_router)
app.include_router(jobs_router)
app.include_router(eco_router)
app.include_router(permissions_router)
app.include_router(teams_router)
app.include_router(compression_router)
app.include_router(replay_router)


class ChatRequest(BaseModel):
    message: str = Field(max_length=50_000)


class ChatResponse(BaseModel):
    response: str


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.post("/api/agent/chat", response_model=ChatResponse)
async def agent_chat(body: ChatRequest, user: User = Depends(get_current_user)):
    if _lane_queue:
        result = await _lane_queue.enqueue(user.id, body.message)
    else:
        # Fallback for standalone gateway (no queue)
        from lazyclaw.llm.router import LLMRouter
        from lazyclaw.runtime.agent import Agent
        from lazyclaw.skills.registry import SkillRegistry

        from lazyclaw.permissions.checker import PermissionChecker

        registry = _shared_registry or SkillRegistry()
        if not _shared_registry:
            registry.register_defaults(config=_config)
        router = LLMRouter(_config)
        permission_checker = PermissionChecker(_config, registry)
        agent = Agent(_config, router, registry, permission_checker=permission_checker)
        result = await agent.process_message(user.id, body.message)
    return ChatResponse(response=result)
