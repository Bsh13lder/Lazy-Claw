from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from lazyclaw.config import load_config
from lazyclaw.db.connection import init_db
from lazyclaw.gateway.auth import User, auth_router, get_current_user
from lazyclaw.gateway.routes.memory import router as memory_router
from lazyclaw.gateway.routes.skills import router as skills_router
from lazyclaw.gateway.routes.vault import router as vault_router
from lazyclaw.gateway.routes.browser import router as browser_router
from lazyclaw.gateway.routes.connector import router as connector_router
from lazyclaw.gateway.routes.connector import ws_router as connector_ws_router
from lazyclaw.llm.model_manager import seed_default_models

logger = logging.getLogger(__name__)

_config = load_config()

# Lane queue reference — set by cli.py at startup
_lane_queue = None


def set_lane_queue(queue) -> None:
    """Called by cli.py to inject the shared LaneQueue."""
    global _lane_queue
    _lane_queue = queue


@asynccontextmanager
async def lifespan(application: FastAPI):
    await init_db(_config)
    await seed_default_models(_config)
    logger.info("Database initialized, models seeded")
    yield


app = FastAPI(title="LazyClaw", version="0.1.0", lifespan=lifespan)

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


class ChatRequest(BaseModel):
    message: str


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

        registry = SkillRegistry()
        registry.register_defaults(config=_config)
        router = LLMRouter(_config)
        agent = Agent(_config, router, registry)
        result = await agent.process_message(user.id, body.message)
    return ChatResponse(response=result)
