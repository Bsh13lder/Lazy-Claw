from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from lazyclaw.config import load_config
from lazyclaw.db.connection import init_db
from lazyclaw.llm.router import LLMRouter
from lazyclaw.runtime.agent import Agent
from lazyclaw.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)

_config = load_config()
_registry = SkillRegistry()
_registry.register_defaults()


@asynccontextmanager
async def lifespan(application: FastAPI):
    await init_db(_config)
    logger.info("Database initialized")
    yield


app = FastAPI(title="LazyClaw", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_config.cors_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.post("/api/agent/chat", response_model=ChatResponse)
async def agent_chat(body: ChatRequest):
    router = LLMRouter(_config)
    agent = Agent(_config, router, _registry)
    result = await agent.process_message("default", body.message)
    return ChatResponse(response=result)
