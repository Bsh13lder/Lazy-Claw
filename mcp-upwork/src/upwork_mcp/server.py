"""Upwork MCP Server - Main entry point."""

import argparse
import asyncio
from typing import Annotated
from mcp.server.fastmcp import FastMCP
from pydantic import Field

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
    MessagesParams,
    SendMessageParams,
    get_messages,
    get_conversation_messages,
    send_message,
    get_unread_count,
)
from .tools.contracts import (
    ContractsParams,
    get_contracts,
    get_contract_details,
    get_work_diary,
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
    params = JobSearchParams(
        query=query,
        source=source,
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
) -> dict:
    """Send a message in an Upwork conversation.

    Returns send status.
    """
    params = SendMessageParams(room_id=room_id, message=message)
    return await send_message(params)


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


def main():
    """Main entry point for CLI."""
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
