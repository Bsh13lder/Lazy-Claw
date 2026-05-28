"""Project budget + expense manager.

A "project" mirrors the free-text ``tasks.category``: ``projects.name_key`` ==
``casefold(category)``, so a project can pre-exist a task and tasks
auto-associate by category match. Budgets + expenses are encrypted at rest
(amounts stay plaintext so totals SUM in SQL, like ``pipeline_deals.amount``);
every expense mirrors to a LazyBrain note that wikilinks back to the project
page so the knowledge-graph shows where money was spent.
"""
