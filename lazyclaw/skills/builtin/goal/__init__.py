"""Goal Executor skills — autonomous high-level objective workflow.

Six skills wrap :mod:`lazyclaw.runtime.goal_executor`:

- :class:`StartGoalSkill` — draft a plan, surface the batch-ask card.
- :class:`AnswerGoalQuestionsSkill` — submit batch answers.
- :class:`GoalStatusSkill` — render progress (no LLM).
- :class:`ListGoalsSkill` — table of recent goals.
- :class:`AbortGoalSkill` — terminate a goal.
- :class:`GoalProgressReportSkill` — digest of all active goals; designed
  to be called from a user-wired ``[GOAL_PROGRESS]`` cron via the existing
  ``ScheduleJobSkill``.
"""

from lazyclaw.skills.builtin.goal.start_skill import StartGoalSkill
from lazyclaw.skills.builtin.goal.answer_skill import AnswerGoalQuestionsSkill
from lazyclaw.skills.builtin.goal.status_skill import GoalStatusSkill
from lazyclaw.skills.builtin.goal.list_skill import ListGoalsSkill
from lazyclaw.skills.builtin.goal.abort_skill import AbortGoalSkill
from lazyclaw.skills.builtin.goal.progress_report_skill import GoalProgressReportSkill

__all__ = [
    "StartGoalSkill",
    "AnswerGoalQuestionsSkill",
    "GoalStatusSkill",
    "ListGoalsSkill",
    "AbortGoalSkill",
    "GoalProgressReportSkill",
]
