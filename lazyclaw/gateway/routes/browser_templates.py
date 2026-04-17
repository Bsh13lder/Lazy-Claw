"""Browser template REST API — saved-agent CRUD + run + watch."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException

from lazyclaw.browser import templates as tpl_store
from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/browser/templates", tags=["browser-templates"])

_config = load_config()


@router.get("")
async def list_templates(user: User = Depends(get_current_user)):
    items = await tpl_store.list_templates(_config, user.id)
    return {"templates": items}


@router.post("/seed")
async def seed_templates(user: User = Depends(get_current_user)):
    """Install the bundled example templates (Cita Previa, Doctoralia, ...).
    Skips any template name that already exists."""
    created = await tpl_store.seed_examples(_config, user.id)
    return {"created": [t["name"] for t in created]}


@router.post("/from-current-session")
async def create_from_current_session(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
):
    """Capture the user's in-flight browser flow as a template.

    Backs the BrowserCanvas '💾 Save as template' button. Pulls recent
    events from the per-user bus, synthesizes a draft (setup_urls +
    checkpoints + LLM-drafted playbook), and persists it.
    """
    name = (payload or {}).get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    from lazyclaw.browser.template_synth import synthesize_template_from_events
    from lazyclaw.llm.router import LLMRouter

    router_llm = LLMRouter(_config)
    try:
        draft = await synthesize_template_from_events(
            _config, router_llm, user.id, name=name,
        )
    except Exception as exc:
        logger.warning("from-current-session synthesis failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Synthesis failed: {exc}")

    if draft is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "No recent browser activity to capture. Open a page and click "
                "through the flow first, then Save as template."
            ),
        )

    try:
        tpl = await tpl_store.create_template(
            _config, user.id,
            name=draft.name,
            icon=draft.icon,
            playbook=draft.playbook,
            setup_urls=draft.setup_urls,
            checkpoints=draft.checkpoints,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "template": tpl,
        "captured": {
            "event_count": draft.event_count,
            "url_count": len(draft.setup_urls),
            "checkpoint_count": len(draft.checkpoints),
        },
    }


@router.post("/from-prompt")
async def create_from_prompt(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
):
    """Generate a NON-persisted template draft from a one-line description.

    Backs the '✨ Create with AI' dialog on the Templates page. The user
    reviews + edits the draft in the existing form before saving.
    """
    prompt = (payload or {}).get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    if len(prompt) > 800:
        raise HTTPException(status_code=400, detail="prompt too long (max 800 chars)")

    from lazyclaw.browser.template_synth import draft_template_from_prompt
    from lazyclaw.llm.router import LLMRouter

    router_llm = LLMRouter(_config)
    try:
        draft = await draft_template_from_prompt(
            router_llm, _config, user.id, prompt,
        )
    except Exception as exc:
        logger.warning("from-prompt draft failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Draft failed: {exc}")
    return {"draft": draft}


@router.post("")
async def create_template(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
):
    if not payload.get("name"):
        raise HTTPException(status_code=400, detail="name is required")
    try:
        tpl = await tpl_store.create_template(
            _config, user.id,
            name=payload["name"],
            icon=payload.get("icon"),
            system_prompt=payload.get("system_prompt"),
            setup_urls=payload.get("setup_urls"),
            checkpoints=payload.get("checkpoints"),
            playbook=payload.get("playbook"),
            page_reader_mode=payload.get("page_reader_mode", "auto"),
            watch_url=payload.get("watch_url"),
            watch_extractor=payload.get("watch_extractor"),
            watch_condition=payload.get("watch_condition"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return tpl


@router.get("/{tpl_id}")
async def get_template(tpl_id: str, user: User = Depends(get_current_user)):
    tpl = await tpl_store.get_template(_config, user.id, tpl_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return tpl


@router.patch("/{tpl_id}")
async def update_template(
    tpl_id: str,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
):
    existing = await tpl_store.get_template(_config, user.id, tpl_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Template not found")
    tpl = await tpl_store.update_template(_config, user.id, tpl_id, **payload)
    return tpl


@router.delete("/{tpl_id}")
async def delete_template(tpl_id: str, user: User = Depends(get_current_user)):
    ok = await tpl_store.delete_template(_config, user.id, tpl_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"status": "deleted"}


@router.post("/{tpl_id}/run")
async def run_template(
    tpl_id: str,
    payload: dict = Body(default={}),
    user: User = Depends(get_current_user),
):
    """Hand off the template to the chat: returns the hydrated instruction
    plus a `chat_message` the web UI can drop into the chat input."""
    tpl = await tpl_store.get_template(_config, user.id, tpl_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="Template not found")
    user_input = (payload or {}).get("input")
    instruction = tpl_store.build_run_instruction(tpl, user_input)
    return {
        "template": {"id": tpl["id"], "name": tpl["name"]},
        "instruction": instruction,
        # The web UI can preload this into the chat box for one-click send.
        "chat_message": f"Run my template '{tpl['name']}'"
        + (f": {user_input}" if user_input else ""),
    }
