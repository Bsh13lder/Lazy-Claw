"""Survival instinct skills: job hunting, proposals, work execution, and tracking.

Skills:
  - set_skills_profile: configure freelance profile (name, github, skills, etc.)
  - set_freelance_pitch: NL pitch + AI polish — opens every proposal
  - search_jobs: find matching jobs via JobSpy MCP or browser
  - apply_job: generate cover letter + submit (user must approve)
  - apply_reddit_dm: send Reddit DM via the user's logged-in Brave (with
    approval checkpoint) for [HIRING] posts found by watch_reddit_forhire
  - survival_mode: toggle automatic job hunting cron
  - survival_status: show stats (no LLM, instant)
  - review_deliverable: quality gate before submitting work to client
  - start_gig: begin working on an accepted job (Claude Code MCP)
  - submit_deliverable: deliver completed work to client
  - invoice_client: create and send Stripe invoice
  - draft_freelance_proposal: open job URL, extract brief, draft proposal
  - watch_reddit_forhire: poll Reddit hiring subs, push matching posts
"""

from lazyclaw.skills.builtin.survival.apply_skill import ApplyJobSkill
from lazyclaw.skills.builtin.survival.deliver_skill import SubmitDeliverableSkill
from lazyclaw.skills.builtin.survival.draft_proposal_skill import DraftFreelanceProposalSkill
from lazyclaw.skills.builtin.survival.gig_skill import StartGigSkill
from lazyclaw.skills.builtin.survival.invoice_skill import InvoiceClientSkill
from lazyclaw.skills.builtin.survival.mode_skill import SurvivalModeSkill, SurvivalStatusSkill
from lazyclaw.skills.builtin.survival.pitch_skill import SetFreelancePitchSkill
from lazyclaw.skills.builtin.survival.profile_skill import SetSkillsProfileSkill
from lazyclaw.skills.builtin.survival.upwork_bot_skill import SetUpworkBotBehaviorSkill
from lazyclaw.skills.builtin.survival.upwork_inbox_check import UpworkInboxCheckSkill
from lazyclaw.skills.builtin.survival.reddit_apply_skill import ApplyRedditDmSkill
from lazyclaw.skills.builtin.survival.reddit_watch_skill import WatchRedditForHireSkill
from lazyclaw.skills.builtin.survival.review_skill import ReviewDeliverableSkill
from lazyclaw.skills.builtin.survival.search_skill import SearchJobsSkill

__all__ = [
    "ApplyJobSkill",
    "ApplyRedditDmSkill",
    "DraftFreelanceProposalSkill",
    "InvoiceClientSkill",
    "ReviewDeliverableSkill",
    "SearchJobsSkill",
    "SetFreelancePitchSkill",
    "SetSkillsProfileSkill",
    "SetUpworkBotBehaviorSkill",
    "StartGigSkill",
    "SubmitDeliverableSkill",
    "SurvivalModeSkill",
    "SurvivalStatusSkill",
    "UpworkInboxCheckSkill",
    "WatchRedditForHireSkill",
]
