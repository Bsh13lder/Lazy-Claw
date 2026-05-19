"""Upwork MCP Server - Main entry point."""

import argparse
import asyncio
import inspect
import sys
from typing import Annotated
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from . import __version__

from .browser.client import get_browser, close_browser, UpworkBrowser
from .browser.auth import login_interactive, check_session, logout
from .tools.jobs import JobSearchParams, JobDetailsParams, search_jobs, get_job_details
from .tools.profile import get_my_profile, get_connects_balance, get_profile_stats
from .tools.proposals import (
    ProposalsParams,
    SubmitProposalParams,
    get_proposals,
    get_proposal_details,
    submit_proposal,
    withdraw_proposal,
)
from .tools.messages import (
    EditMessageParams,
    MessagesParams,
    SendMessageParams,
    edit_message,
    get_messages,
    get_conversation_messages,
    send_message,
    get_unread_count,
)
from .tools.contracts import (
    ContractsParams,
    SubmitMilestoneParams,
    get_contracts,
    get_contract_details,
    get_work_diary,
    submit_milestone,
)
from .tools.offers import (
    AcceptOfferParams,
    DeclineOfferParams,
    OffersParams,
    accept_offer,
    decline_offer,
    get_offers,
)

# Initialize FastMCP server
mcp = FastMCP(
    name="upwork-mcp",
    instructions="Upwork MCP Server - Search jobs, manage proposals, messages, and contracts via browser automation",
)


# ============================================================================
# Job Tools
# ============================================================================


@mcp.tool()
async def upwork_search_jobs(
    query: Annotated[
        str,
        Field(
            description=(
                "Keywords to search. Ignored when source='best_matches'. "
                "Required (non-empty) when source='search'."
            ),
        ),
    ] = "",
    source: Annotated[
        str | None,
        Field(
            description=(
                "Which Upwork surface to read from. "
                "'best_matches' (DEFAULT) → /nx/find-work/best-matches — "
                "Upwork's personalized recs honoring the filters the user "
                "set on Upwork's side (fixed-price / hourly / budget "
                "range etc.). Returns ~20-50 jobs. Use when the user says "
                "'find me jobs' / 'any matches' / 'what's new for me'. "
                "'search' → /nx/search/jobs/?q=... — global keyword search "
                "across 100k+ active postings. Use only when the user "
                "explicitly names specific tech ('find python scraping jobs')."
            ),
        ),
    ] = "best_matches",
    category: Annotated[str | None, Field(description="Job category filter")] = None,
    budget_min: Annotated[int | None, Field(description="Minimum budget in USD")] = None,
    budget_max: Annotated[int | None, Field(description="Maximum budget in USD")] = None,
    experience_level: Annotated[
        str | None, Field(description="Experience level: entry, intermediate, or expert")
    ] = None,
    job_type: Annotated[str | None, Field(description="Job type: hourly or fixed")] = None,
    limit: Annotated[
        int,
        Field(
            description=(
                "Maximum number of results (1-50). Values above 50 are "
                "silently clamped to 50 — Upwork's search page returns "
                "~30-50 results per fetch anyway, so a higher request "
                "wouldn't yield more jobs. Values below 1 are clamped to 1."
            ),
            ge=1,
        ),
    ] = 20,
) -> list[dict]:
    """Search for jobs on Upwork.

    Default mode is 'best_matches' (Upwork's personalized recommendations
    that already respect the user's saved profile filters). Pass
    source='search' with a non-empty query to run a global keyword search.

    ``limit`` is silently clamped to the [1, 50] range — Upwork's search
    page itself caps at ~50 jobs per fetch, so requesting 100 wouldn't
    return more. We clamp instead of erroring because the brain has been
    observed passing limit=100 and the hard rejection killed the search
    entirely (verified live 2026-05-13).

    Returns a list of job summaries with title, budget, client info, and URL.
    """
    # Clamp limit silently — JobSearchParams validates ge=1, le=50, so
    # without this clamp any caller passing limit>50 hits a Pydantic
    # ValidationError and the search aborts with no results.
    safe_limit = max(1, min(int(limit), 50))
    # Coerce explicit None → wrapper default. Pydantic applies defaults
    # only when a field is OMITTED, not when it's explicitly null —
    # so when Sonnet calls upwork_search_jobs without `source`, the
    # SDK still passes source=None into this wrapper, which then
    # forwards None into JobSearchParams.source (typed `str`, not
    # optional) → ValidationError "Input should be a valid string,
    # input_value=None". Observed twice on 2026-05-13.
    safe_source = source if isinstance(source, str) and source else "best_matches"
    params = JobSearchParams(
        query=query,
        source=safe_source,
        category=category,
        budget_min=budget_min,
        budget_max=budget_max,
        experience_level=experience_level,
        job_type=job_type,
        limit=safe_limit,
    )
    return await search_jobs(params)


@mcp.tool()
async def upwork_get_job_details(
    job_url: Annotated[str, Field(description="Full Upwork job URL or job ID")]
) -> dict:
    """Get detailed information about a specific Upwork job posting.

    Returns comprehensive job details including description, client history,
    skills required, and application requirements.
    """
    params = JobDetailsParams(job_url=job_url)
    return await get_job_details(params)


# ============================================================================
# Profile Tools
# ============================================================================


@mcp.tool()
async def upwork_get_my_profile() -> dict:
    """Get your Upwork freelancer profile information.

    Returns profile data including name, title, hourly rate, JSS score,
    availability status, and skill tags.
    """
    return await get_my_profile()


@mcp.tool()
async def upwork_get_connects_balance() -> dict:
    """Get current Upwork Connects balance.

    Returns the number of available connects.
    """
    return await get_connects_balance()


@mcp.tool()
async def upwork_get_profile_stats() -> dict:
    """Get profile statistics including earnings and work history.

    Returns stats like total earnings, hours worked, jobs completed.
    """
    return await get_profile_stats()


# ============================================================================
# Proposal Tools
# ============================================================================


@mcp.tool()
async def upwork_get_proposals(
    status: Annotated[
        str, Field(description="Filter by status: active, submitted, archived, or all")
    ] = "active",
    limit: Annotated[
        int,
        Field(description="Maximum number of results (clamped to 1-50)", ge=1),
    ] = 20,
) -> list[dict]:
    """Get your submitted proposals on Upwork.

    Returns a list of proposals with job title, status, bid amount, and dates.
    ``limit`` is silently clamped to the [1, 50] range.
    """
    safe_limit = max(1, min(int(limit), 50))
    params = ProposalsParams(status=status, limit=safe_limit)
    return await get_proposals(params)


@mcp.tool()
async def upwork_get_proposal_details(
    proposal_url: Annotated[str, Field(description="URL to the proposal")]
) -> dict:
    """Get detailed information about a specific proposal.

    Returns details including cover letter, bid, and any messages.
    """
    return await get_proposal_details(proposal_url)


@mcp.tool()
async def upwork_submit_proposal(
    job_url: Annotated[str, Field(description="Full Upwork job URL")],
    cover_letter: Annotated[str, Field(description="Cover letter content")],
    rate: Annotated[float | None, Field(description="Proposed hourly rate (for hourly jobs)")] = None,
    bid: Annotated[float | None, Field(description="Bid amount (for fixed-price jobs)")] = None,
    answers: Annotated[list[str] | None, Field(description="Answers to screening questions")] = None,
    connects_to_send: Annotated[
        int | None,
        Field(
            description=(
                "Total Connects to spend on this proposal (mandatory + "
                "boost). Upwork auto-suggests a default per job (often 6); "
                "pass a LOWER number to spend the minimum (saves your "
                "balance for more proposals) or a HIGHER number to boost "
                "visibility on competitive postings. None keeps Upwork's "
                "auto-suggested default. Set to the user's remaining "
                "balance to avoid overspend."
            ),
        ),
    ] = None,
    milestone_due_date: Annotated[
        str | None,
        Field(description="Milestone due date (MM/DD/YYYY) for fixed-price. None → +30 days."),
    ] = None,
    milestone_description: Annotated[
        str | None,
        Field(description="Single-milestone description for fixed-price jobs. None → 'Project completion'."),
    ] = None,
    project_duration: Annotated[
        str | None,
        Field(description="'Less than 1 month' | '1 to 3 months' | '3 to 6 months' | 'More than 6 months'. None → 'Less than 1 month'."),
    ] = None,
) -> dict:
    """Submit a proposal to an Upwork job.

    IMPORTANT: This is a sensitive action that will spend Connects.
    Make sure the cover letter and rate/bid are correct before submitting.
    Pass connects_to_send if you want to cap or boost the connect spend
    (omit for Upwork's per-job auto-suggested default).

    Returns submission status and connects used.
    """
    params = SubmitProposalParams(
        job_url=job_url,
        cover_letter=cover_letter,
        rate=rate,
        bid=bid,
        answers=answers,
        connects_to_send=connects_to_send,
        milestone_due_date=milestone_due_date,
        milestone_description=milestone_description,
        project_duration=project_duration,
    )
    return await submit_proposal(params)


@mcp.tool()
async def upwork_withdraw_proposal(
    proposal_url: Annotated[str, Field(description="URL to the proposal to withdraw, e.g. https://www.upwork.com/nx/proposals/<id>")],
    reason: Annotated[
        str,
        Field(
            description=(
                "Reason for withdrawal. One of: "
                "'Applied by mistake' (default), "
                "'Rate too low', 'Scheduling conflict with client', "
                "'Unresponsive client', 'Inappropriate client behavior', 'Other'."
            ),
        ),
    ] = "Applied by mistake",
) -> dict:
    """Withdraw a submitted proposal via Upwork's two-step modal flow.

    Opens the proposal page, clicks Withdraw, picks the reason, confirms.
    """
    return await withdraw_proposal(proposal_url, reason)


# ============================================================================
# Message Tools
# ============================================================================


@mcp.tool()
async def upwork_get_messages(
    room_id: Annotated[str | None, Field(description="Specific chat room ID or URL")] = None,
    unread_only: Annotated[bool, Field(description="Only show unread messages")] = False,
    limit: Annotated[
        int,
        Field(description="Maximum conversations to return (clamped to 1-50)", ge=1),
    ] = 20,
) -> list[dict]:
    """Get messages from Upwork inbox.

    Returns a list of conversations with last message, sender info, and unread status.
    ``limit`` is silently clamped to the [1, 50] range.
    """
    safe_limit = max(1, min(int(limit), 50))
    params = MessagesParams(room_id=room_id, unread_only=unread_only, limit=safe_limit)
    return await get_messages(params)


@mcp.tool()
async def upwork_get_conversation(
    room_id: Annotated[str, Field(description="Chat room ID or URL")],
    limit: Annotated[
        int,
        Field(description="Maximum messages to return (clamped to 1-100)", ge=1),
    ] = 50,
    me_name: Annotated[
        str | None,
        Field(
            description=(
                "Caller's own Upwork display name (e.g. 'Vato Tchipa'). "
                "When provided, each returned message gets is_mine=True "
                "when its sender matches this name (tolerant: first-name "
                "prefix match handles 'Vato T.' vs 'Vato Tchipa'). "
                "Required for the drafter to know who said what last; "
                "None falls back to legacy class-hint detection (stale "
                "on the 2026 layout, always returns False)."
            ),
        ),
    ] = None,
) -> dict:
    """Get all messages in a specific conversation.

    Returns conversation details with full message history.
    ``limit`` is silently clamped to the [1, 100] range.
    """
    safe_limit = max(1, min(int(limit), 100))
    return await get_conversation_messages(room_id, safe_limit, me_name=me_name)


@mcp.tool()
async def upwork_send_message(
    room_id: Annotated[str, Field(description="Chat room ID or URL")],
    message: Annotated[str, Field(description="Message content to send")],
    draft_only: Annotated[
        bool,
        Field(
            description=(
                "When true, type the message into the Upwork compose "
                "box but DO NOT click Send. Use for human-in-loop "
                "review — caller drafts, user inspects in their live "
                "Brave tab, then sends manually. Returns status='drafted'."
            ),
        ),
    ] = False,
) -> dict:
    """Send a message in an Upwork conversation.

    Multi-line messages use Shift+Enter between lines (Tiptap-safe) —
    one Upwork bubble per call, never per line.

    Returns send status. With draft_only=True, returns status='drafted'
    without clicking Send.
    """
    params = SendMessageParams(
        room_id=room_id, message=message, draft_only=draft_only,
    )
    return await send_message(params)


@mcp.tool()
async def upwork_edit_message(
    room_id: Annotated[str, Field(description="Chat room ID or URL")],
    message_index: Annotated[
        int,
        Field(
            description=(
                "Which of YOUR (is_mine=True) messages to edit, counting "
                "from most recent backwards. 0 = your last message."
            ),
            ge=0,
        ),
    ],
    new_content: Annotated[
        str,
        Field(description="Replacement text. Multi-line is Shift+Enter-safe."),
    ],
    draft_only: Annotated[
        bool,
        Field(
            description=(
                "When true, open the edit modal + type replacement but "
                "DO NOT save. User confirms manually. Returns "
                "status='drafted_edit'."
            ),
        ),
    ] = False,
) -> dict:
    """Edit one of your already-sent Upwork messages.

    Upwork's 2026 UI allows editing within ~60 minutes of sending.
    Past the window, returns status='expired' — caller should
    send a correction message instead.

    Same Tiptap-safe typing path as send_message — multi-line edits
    use Shift+Enter between lines, never accidentally trigger save
    mid-edit. URL and product-pitch guards fire BEFORE any browser
    nav.
    """
    params = EditMessageParams(
        room_id=room_id,
        message_index=message_index,
        new_content=new_content,
        draft_only=draft_only,
    )
    return await edit_message(params)


@mcp.tool()
async def upwork_get_unread_count() -> dict:
    """Get count of unread messages.

    Returns total unread message count.
    """
    return await get_unread_count()


# ============================================================================
# Contract Tools
# ============================================================================


@mcp.tool()
async def upwork_get_contracts(
    status: Annotated[str, Field(description="Filter by status: active, ended, or all")] = "active",
    limit: Annotated[
        int,
        Field(description="Maximum number of results (clamped to 1-50)", ge=1),
    ] = 20,
) -> list[dict]:
    """Get your Upwork contracts.

    Returns a list of contracts with client name, job title, status, and earnings.
    ``limit`` is silently clamped to the [1, 50] range.
    """
    safe_limit = max(1, min(int(limit), 50))
    params = ContractsParams(status=status, limit=safe_limit)
    return await get_contracts(params)


@mcp.tool()
async def upwork_get_contract_details(
    contract_url: Annotated[str, Field(description="URL to the contract")]
) -> dict:
    """Get detailed information about a specific contract.

    Returns full contract details including milestones, hours logged, and feedback.
    """
    return await get_contract_details(contract_url)


@mcp.tool()
async def upwork_get_work_diary(
    contract_url: Annotated[str, Field(description="URL to the contract")],
    week_offset: Annotated[int, Field(description="0 for current week, 1 for last week, etc.")] = 0,
) -> dict:
    """Get work diary entries for a contract.

    Returns work diary with daily hours and earnings.
    """
    return await get_work_diary(contract_url, week_offset)


# ============================================================================
# Offer Tools  (added 2026-05-19 — core gig-pipeline flow)
# ============================================================================
#
# Offers are sent by clients to specific freelancers ("here's $120 to do X")
# and live in their own page until accepted/declined. Accepting creates a
# contract; declining notifies the client. Both writes are sensitive and
# support draft_only for human-in-loop staging.


@mcp.tool()
async def upwork_get_offers(
    status: Annotated[
        str,
        Field(
            description=(
                "Filter by offer status. 'pending' (DEFAULT) = offers "
                "awaiting your response — the actionable bucket. "
                "'accepted' = already contracts (use upwork_get_contracts "
                "for details). 'declined' = turned down. 'all' = everything."
            ),
        ),
    ] = "pending",
    limit: Annotated[
        int,
        Field(description="Maximum number of offers (clamped 1-50)", ge=1),
    ] = 20,
) -> list[dict]:
    """List Upwork offers (client → freelancer).

    Probes the known offer-list URLs in order and returns the first
    non-empty result. Status filter applied client-side because Upwork's
    ``?status=`` query is flaky across layout migrations.

    Use this before upwork_accept_offer / upwork_decline_offer — the
    'url' field in each returned offer is what you pass to those tools.
    """
    safe_limit = max(1, min(int(limit), 50))
    safe_status = status if status in ("pending", "accepted", "declined", "all") else "pending"
    params = OffersParams(status=safe_status, limit=safe_limit)
    return await get_offers(params)


@mcp.tool()
async def upwork_accept_offer(
    offer_url: Annotated[
        str,
        Field(
            description=(
                "Full Upwork offer URL (from upwork_get_offers). Must "
                "be on upwork.com — non-Upwork URLs are refused."
            ),
        ),
    ],
    draft_only: Annotated[
        bool,
        Field(
            description=(
                "When true, navigate + open the Accept confirm modal "
                "but DO NOT click the final Accept button. User "
                "confirms manually in their live Brave tab. Returns "
                "status='drafted_accept'."
            ),
        ),
    ] = False,
) -> dict:
    """Accept an Upwork offer (CREATES a binding fixed-price/hourly contract).

    IMPORTANT: This is a sensitive write action. Once accepted the
    contract exists, the freelancer is committed to scope/budget, and
    declining requires going through Upwork dispute resolution.

    Returns ``{status, contract_url?, message}``. Statuses:
      - ``"accepted"`` — final click went through
      - ``"drafted_accept"`` — modal staged
      - ``"already_accepted"`` — offer was already a contract
      - ``"not_found"`` — Accept button missing
      - ``"error"`` — navigation or selector failure
    """
    params = AcceptOfferParams(offer_url=offer_url, draft_only=draft_only)
    return await accept_offer(params)


@mcp.tool()
async def upwork_decline_offer(
    offer_url: Annotated[
        str, Field(description="Full Upwork offer URL (from upwork_get_offers)."),
    ],
    reason: Annotated[
        str,
        Field(
            description=(
                "Short, neutral reason shown in the client's notification. "
                "Default 'Not the right fit'. URL/product-pitch guards "
                "apply — external URLs and 'lazyclaw' are refused."
            ),
        ),
    ] = "Not the right fit",
    draft_only: Annotated[
        bool,
        Field(
            description=(
                "When true, open the decline modal + fill the reason "
                "but DO NOT click the final Decline button."
            ),
        ),
    ] = False,
) -> dict:
    """Decline an Upwork offer.

    Returns ``{status, message}``. Statuses:
      - ``"declined"`` — final click went through
      - ``"drafted_decline"`` — modal staged
      - ``"blocked"`` — reason contained a URL or product-pitch token
      - ``"not_found"`` — Decline button missing
      - ``"error"`` — exception during the flow
    """
    params = DeclineOfferParams(
        offer_url=offer_url, reason=reason, draft_only=draft_only,
    )
    return await decline_offer(params)


# ============================================================================
# Milestone Submission  (added 2026-05-19 — completes payment pipeline)
# ============================================================================


@mcp.tool()
async def upwork_submit_milestone(
    contract_url: Annotated[
        str,
        Field(
            description=(
                "Full Upwork contract URL. Must be on upwork.com. The "
                "tool targets the first active/funded milestone unless "
                "milestone_id is specified."
            ),
        ),
    ],
    message: Annotated[
        str,
        Field(
            description=(
                "Description for the client when the milestone lands "
                "in their review queue. Be brief — what was delivered "
                "and how to verify. URL + product-pitch guards apply."
            ),
        ),
    ] = "",
    milestone_id: Annotated[
        str | None,
        Field(
            description=(
                "Optional milestone identifier when the contract has "
                "multiple milestones. None targets the first active row."
            ),
        ),
    ] = None,
    attachments: Annotated[
        list[str] | None,
        Field(
            description=(
                "Optional list of local file paths (max ~5 per Upwork "
                "policy). Each is verified to exist before opening "
                "the modal. None submits message-only."
            ),
        ),
    ] = None,
    draft_only: Annotated[
        bool,
        Field(
            description=(
                "When true, open the submit modal + type description + "
                "attach files but DO NOT click final Submit. User "
                "finalizes manually. Returns status='drafted_submit'."
            ),
        ),
    ] = False,
) -> dict:
    """Submit a funded milestone for client review and payment release.

    IMPORTANT: This is a sensitive write action — clicking Submit starts
    Upwork's 14-day review timer. Client either approves (funds release)
    or disputes. If they don't act, funds auto-release on day 14.

    Returns ``{status, message, attached_count?}``. Statuses:
      - ``"submitted"`` — final click went through
      - ``"drafted_submit"`` — modal staged with description + files
      - ``"blocked"`` — message contained a URL or product-pitch token
      - ``"not_found"`` — Request Payment button or milestone row missing
      - ``"missing_attachment"`` — a path in attachments doesn't exist
      - ``"error"`` — selector drift or navigation failure
    """
    params = SubmitMilestoneParams(
        contract_url=contract_url,
        message=message,
        milestone_id=milestone_id,
        attachments=attachments,
        draft_only=draft_only,
    )
    return await submit_milestone(params)


# ============================================================================
# Session Tools
# ============================================================================


@mcp.tool()
async def upwork_check_session() -> dict:
    """Check if the current Upwork session is valid.

    Returns session status and whether re-login is needed.
    """
    browser = get_browser()
    try:
        await browser.start()
        logged_in = await browser.is_logged_in()
        return {
            "logged_in": logged_in,
            "message": "Session is valid" if logged_in else "Session expired. Run 'uvx upwork-mcp --login' to authenticate.",
        }
    except Exception as e:
        return {"logged_in": False, "error": str(e)}


@mcp.tool()
async def upwork_close_session() -> dict:
    """Close browser session and cleanup resources.

    Call this when you're done using Upwork tools to free up resources.
    """
    await close_browser()
    return {"status": "closed", "message": "Browser session closed successfully"}


# ============================================================================
# CLI Entry Point
# ============================================================================


def _self_check() -> None:
    """Verify the deployed mcp-upwork has critical fixes before serving.

    History — 2026-05-17 14:01: a multi-line Upwork draft fragmented into
    8 separate bubbles because the running Docker image predated commit
    6aac606 (which added :func:`_type_with_soft_breaks` — Shift+Enter for
    Tiptap soft-breaks instead of raw Enter, which Upwork's editor treats
    as Send). The container hadn't been rebuilt; the source-tree fix was
    on disk but pip-installed code in the image was stale. No alarm
    fired — fragmentation looked identical to a brain glitch.

    This guard makes that class of stale-image regression LOUD:
      1. Log version + helper presence to stderr on every startup so a
         stale image is visible in the user's MCP logs.
      2. If :func:`_type_with_soft_breaks` is missing OR ``send_message``
         doesn't reference it, abort startup. A no-op MCP is annoying;
         a silent message-spam MCP is reputation damage.

    stdout is reserved for the MCP stdio protocol — all output goes to
    stderr so it doesn't corrupt MCP framing.
    """
    from .tools import messages as _msgs

    helper_present = hasattr(_msgs, "_type_with_soft_breaks")
    send_uses_helper = False
    if helper_present:
        try:
            send_uses_helper = (
                "_type_with_soft_breaks" in inspect.getsource(_msgs.send_message)
            )
        except (OSError, TypeError):
            send_uses_helper = False  # source unavailable → treat as drift

    print(
        f"[upwork-mcp] startup self-check: version={__version__} "
        f"_type_with_soft_breaks={'YES' if helper_present else 'MISSING'} "
        f"send_message_uses_helper={'YES' if send_uses_helper else 'NO'}",
        file=sys.stderr,
    )

    if not (helper_present and send_uses_helper):
        print(
            "[upwork-mcp] FATAL: deployed code is missing the Tiptap "
            "soft-break fix (commit 6aac606). Multi-line message sends "
            "would fragment into one bubble per newline. Rebuild the "
            "Docker image with the current source tree: "
            "`docker compose build lazyclaw && docker compose up -d`. "
            "Refusing to serve.",
            file=sys.stderr,
        )
        sys.exit(2)


def main():
    """Main entry point for CLI."""
    _self_check()
    parser = argparse.ArgumentParser(
        description="Upwork MCP Server - Browser automation for Upwork",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  upwork-mcp --login        Open browser for manual login
  upwork-mcp --check        Check if session is valid
  upwork-mcp --logout       Clear saved session
  upwork-mcp                Start MCP server (default)
        """,
    )

    parser.add_argument(
        "--login",
        action="store_true",
        help="Open browser for manual login to Upwork",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if current session is valid",
    )
    parser.add_argument(
        "--logout",
        action="store_true",
        help="Clear saved session",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Show browser window (for debugging)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30000,
        help="Page timeout in milliseconds (default: 30000)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio"],
        default="stdio",
        help="MCP transport type (default: stdio)",
    )

    args = parser.parse_args()

    if args.login:
        asyncio.run(login_interactive())
        return

    if args.check:
        async def check():
            result = await check_session()
            if result:
                print("✓ Session is valid")
            else:
                print("✗ Session expired or invalid")
                print("  Run 'uvx upwork-mcp --login' to authenticate")

        asyncio.run(check())
        return

    if args.logout:
        asyncio.run(logout())
        return

    # Initialize browser with settings
    browser = get_browser(
        headless=not args.no_headless,
        timeout=args.timeout,
    )

    # Run MCP server
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
