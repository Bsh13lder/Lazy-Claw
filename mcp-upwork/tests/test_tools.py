"""Tests for MCP tools."""

import pytest
from pydantic import ValidationError


def test_job_search_params_validation():
    """Test JobSearchParams validation."""
    from upwork_mcp.tools.jobs import JobSearchParams

    # Valid params
    params = JobSearchParams(query="python developer")
    assert params.query == "python developer"
    assert params.limit == 20

    # With optional params
    params = JobSearchParams(
        query="python",
        budget_min=100,
        experience_level="expert",
        limit=10,
    )
    assert params.budget_min == 100
    assert params.experience_level == "expert"
    assert params.limit == 10

    # Invalid limit
    with pytest.raises(ValidationError):
        JobSearchParams(query="test", limit=100)


def test_job_details_params_validation():
    """Test JobDetailsParams validation."""
    from upwork_mcp.tools.jobs import JobDetailsParams

    # Valid URL
    params = JobDetailsParams(job_url="https://www.upwork.com/jobs/~01234567890")
    assert "upwork.com" in params.job_url

    # Job ID only
    params = JobDetailsParams(job_url="~01234567890")
    assert params.job_url == "~01234567890"


def test_proposals_params_validation():
    """Test ProposalsParams validation."""
    from upwork_mcp.tools.proposals import ProposalsParams

    params = ProposalsParams()
    assert params.status == "active"
    assert params.limit == 20

    params = ProposalsParams(status="archived", limit=10)
    assert params.status == "archived"


def test_messages_params_validation():
    """Test MessagesParams validation."""
    from upwork_mcp.tools.messages import MessagesParams

    params = MessagesParams()
    assert params.unread_only is False
    assert params.limit == 20

    params = MessagesParams(unread_only=True)
    assert params.unread_only is True


def test_contracts_params_validation():
    """Test ContractsParams validation."""
    from upwork_mcp.tools.contracts import ContractsParams

    params = ContractsParams()
    assert params.status == "active"

    params = ContractsParams(status="ended")
    assert params.status == "ended"
