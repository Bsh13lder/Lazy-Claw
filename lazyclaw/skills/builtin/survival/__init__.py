"""Survival instinct skills: job hunting, proposals, work execution, and tracking.

Nine skills:
  - set_skills_profile: configure freelance profile
  - search_jobs: find matching jobs via JobSpy MCP or browser
  - apply_job: generate cover letter + submit (user must approve)
  - survival_mode: toggle automatic job hunting cron
  - survival_status: show stats (no LLM, instant)
  - review_deliverable: quality gate before submitting work to client
  - start_gig: begin working on an accepted job (Claude Code MCP)
  - submit_deliverable: deliver completed work to client
  - invoice_client: create and send Stripe invoice
"""

from lazyclaw.skills.builtin.survival.apply_skill import ApplyJobSkill
from lazyclaw.skills.builtin.survival.deliver_skill import SubmitDeliverableSkill
from lazyclaw.skills.builtin.survival.gig_skill import StartGigSkill
from lazyclaw.skills.builtin.survival.invoice_skill import InvoiceClientSkill
from lazyclaw.skills.builtin.survival.mode_skill import SurvivalModeSkill, SurvivalStatusSkill
from lazyclaw.skills.builtin.survival.profile_skill import SetSkillsProfileSkill
from lazyclaw.skills.builtin.survival.review_skill import ReviewDeliverableSkill
from lazyclaw.skills.builtin.survival.search_skill import SearchJobsSkill

__all__ = [
    "ApplyJobSkill",
    "InvoiceClientSkill",
    "ReviewDeliverableSkill",
    "SearchJobsSkill",
    "SetSkillsProfileSkill",
    "StartGigSkill",
    "SubmitDeliverableSkill",
    "SurvivalModeSkill",
    "SurvivalStatusSkill",
]
