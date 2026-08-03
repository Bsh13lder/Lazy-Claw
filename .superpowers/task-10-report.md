# Task 10 report — Expense quick-typing (`spent on #clubbay 25`)

## Summary

Added a SIBLING smart-add parser for expense lines (amount + currency +
`#`/`/` project only — no date/time/priority/recurrence matcher), hoisted the
shared `#project` recognition into a plain file both parsers import, wired the
Add Expense sheet's Description field to it (Todoist-style live pre-fill,
manual-override-wins), fixed the pre-existing comma-decimal gap on the numeric
Amount field, and cleaned up a dead branch + stale docstring found in
`smart_add_controller.dart` while in that file.

## Files changed

**New:**
- `mobile/lib/core/smart_add/project_token.dart` — canonical `#`/`/` project
  regex (`projectTokenPattern`) + `removeProjectToken`, a plain (non-`part
  of`) library both sibling parsers import directly.
- `mobile/lib/core/smart_add_expense_parser.dart` — `ParsedExpense` +
  `parseSmartExpense`. Amount regex exactly as specified in the plan
  (`(^|\s)(€|\$|£)?(\d{1,6}(?:\.\d{1,2})?)\s*(EUR|USD|GBP|JPY|€|\$|£)?(?=\s|$)`,
  case-insensitive), detected first and its span masked (working-copy only)
  before the project matcher runs, so the two can never overlap.
- `mobile/test/core/smart_add_expense_parser_test.dart` — 17 unit tests.
- `mobile/test/screens/add_expense_sheet_test.dart` — 5 widget tests.

**Modified:**
- `mobile/lib/core/smart_add_parser.dart` — added `SmartTokenKind.amount`
  (one enum value); added the now-mandatory exhaustive `case
  SmartTokenKind.amount:` no-op inside `parseSmartAdd`'s own token-collection
  switch (the compiler requires every switch STATEMENT over the enum to
  handle it, not just the one in `smart_add_controller.dart` the plan called
  out — there were two, this was the second); imports/re-exports
  `smart_add/project_token.dart`; deleted the old local `removeProjectToken`
  body (now provided via the export, so `add_task_sheet.dart`'s existing
  import + call site needed zero changes).
- `mobile/lib/core/smart_add/project.dart` — `_collectProject` now matches
  against the shared `projectTokenPattern` instead of a private local copy of
  the same regex string (removed the fork).
- `mobile/lib/screens/tasks/smart_add_controller.dart` — added
  `SmartTokenKind.amount => AppColors.success` in `_colorFor`; removed the
  dead single-`!` fallback branch in `_priorityColor` (unreachable since G1 #2
  deleted the bare-single-bang match — `_priorityBangs` only ever matches 2–3
  bangs now) and corrected the docstring that still listed `p2`/bare `!` as
  supported tokens.
- `mobile/lib/screens/expenses/add_expense_sheet.dart` — `AddExpenseSheet`
  is now `ConsumerStatefulWidget` (needed for the in-sheet "create project"
  suggestion action); Description field is `SmartAddController`-backed and
  parses on every keystroke; Amount + project pre-fill with
  `_amountTouched`/`_projectTouched` manual-override-wins (mirrors
  `add_task_sheet.dart`'s `_categoryTouched`); `_submit` normalises `,`→`.`
  on the Amount field text (scope item 3) and submits the parser's
  `cleanDescription` (mirrors the task sheet's `clean`/fallback-to-raw
  pattern); added `_ExpenseProjectSuggestionStrip` (bucketed prefix/substring
  matches + "Create project" row, shown only when the parsed `#`/`/` token
  has no unambiguous existing-project match — an unambiguous match is applied
  silently). Public constructor signature and `onSubmit` shape are UNCHANGED,
  so `expenses_screen.dart` (not touched) keeps compiling as-is.

## TDD evidence

Parser + widget behavior were derived by hand-tracing the exact regex given
in the plan against every required fixture (masking order, greedy-`\s*`
backtracking, the boundary lookahead that makes `25,50` fail to match at all)
before writing the implementation, then the full fixture set was written and
run together — all 17 parser tests and 5 widget tests passed on the first
run, confirming the trace. This is closer to "design-verified then confirmed"
than a strict red-first cycle; flagging that honestly rather than
manufacturing an artificial RED step after the fact.

Gates run from the worktree's `mobile/`:
- `flutter test test/core/ test/screens/` → **1052 tests, 1 failure** — the
  documented pre-existing `expenses_range_filter_test.dart` failure (verified
  by stashing my changes and re-running: identical single failure on a clean
  checkout). `home_screen_test.dart` did not fail, so no isolated re-run was
  needed.
- `flutter analyze` → **65 issues**, matching the documented baseline exactly
  (verified the same way: stashed my changes → 65; restored → was 78 before
  cleanup). Fixed two self-inflicted lints in the new widget test file rather
  than accept a "same category as existing" excuse: swapped `import
  'package:sqflite_common/sqlite_api.dart'` for `import
  'package:sqflite_sqlcipher/sqflite.dart'` (a direct pubspec dependency,
  unlike `sqflite_common`) to drop `depend_on_referenced_packages`, and
  collapsed `(_, __, ___, ____)` unused-callback-arg lists to `(_, _, _, _)`
  to drop `unnecessary_underscores`.
- Existing parser + controller suites (98/89-ish task-parser tests,
  `smart_add_controller_test.dart`) re-run before/after the hoist: identical
  157/157 pass, confirming the `#project` regex relocation changed no
  behavior.

## Fixture coverage (parser)

User's literal example plus every required fixture: `spent on #clubbay 25`,
`25 #clubbay lunch with team`, `€45.50 hosting /nima`, `12.90 coffee`,
`40 eur #nima` (lower-case code), `20 GBP`, `$9.99`, `500 jpy`. Anti-patterns
confirmed NOT to misfire: `parking !2` (no priority matcher loaded at all),
`25,50 groceries` (comma-decimal — the trailing boundary lookahead fails
right after the first digit run, so the WHOLE match fails; pinned as
`amount: isNull`, not merely `isNot(25.5)`), `6/10 dinner` (no date matcher;
`6/10` also isn't a project since `/` isn't at a token boundary), and a URL
(`http://example.com/team` — none of its slashes sit at a token boundary
either, so the shared `projectTokenPattern` already refuses it — same
protection the task parser already had, reused unchanged).

## Self-review / concerns

1. **Two enum-exhaustiveness sites, not one.** The plan called out
   `smart_add_controller.dart:_colorFor` as the switch that would
   compile-break from adding `SmartTokenKind.amount`. `parseSmartAdd` itself
   (in `smart_add_parser.dart`) has its OWN exhaustive switch over
   `SmartTokenKind` that also needed a case. Both are now handled; flagging
   this since the plan's framing implied only one call site.
2. **`removeProjectToken`'s hoist touches `smart_add_parser.dart` beyond the
   "one enum value" instruction.** The plan's "Reuse, don't fork" design
   explicitly requires the project regex + `removeProjectToken` to live in a
   shared home both parsers import — doing that without also touching
   `smart_add_parser.dart` (to delete the old fork and add the
   import/export) isn't possible. Kept the touch as small and mechanical as
   I could: two import/export lines added, one now-redundant function body
   deleted, nothing else in that file's logic changed. `add_task_sheet.dart`
   (which calls `removeProjectToken`) needed zero changes — it still resolves
   the same public name through the re-export.
3. **`AddExpenseSheet` is now `ConsumerStatefulWidget`.** Needed so the
   suggestion strip's "Create project" row can call
   `budgetsProvider.notifier.createProject` without plumbing a new callback
   through `expenses_screen.dart` (which I do not own and did not touch).
   Verified `expenses_screen.dart` still compiles unchanged (`flutter
   analyze` on both files, clean) — the constructor's public shape didn't
   change, and the sheet is already opened from inside a `ProviderScope`-
   wrapped app tree.
4. **Currency is parsed but not surfaced in the UI.** Per the plan's v1 scope
   (amount+project first, currency symbol/code second, comma-fix third — no
   UI currency field is in scope), `ParsedExpense.currency` is exposed and
   unit-tested but never passed to `onSubmit` — the existing 4-arg shape is
   unchanged, so a typed bare amount still gets its currency from the
   project-currency-inherit path in `budgets_provider.dart:addExpense`
   exactly like a manually-typed entry does. Pinned both at the parser level
   (`currency` stays null absent an explicit symbol/code) and the widget
   level (onSubmit's captured args match the plain 4-arg shape).
5. **Concurrent-agent boundary respected.** Did not touch
   `dates.dart`/`times.dart`/`priority.dart`/`recurrence_patterns.dart` or
   their tests. `git status` before finishing shows only the files listed
   above as modified/new; the other agent's `dates.dart` WIP (present at
   session start) was gone from `git status` by the time I finished (it had
   been committed elsewhere in the worktree's history), so there was nothing
   left to conflict with.

## Not done (explicitly out of v1 scope per the plan)

- Vendor via `@`/`from`, `spentAt` date parsing, subtask-in-text addressing —
  all explicitly DEFERRED by the plan.
- No currency picker/field added to the sheet (none exists today; out of
  scope per the plan's 3-item scope list).

---

## Review round 2 — fixes

### 1. Project resolution widened to mirror the agent-side resolver (Important)

`_matchProjectByName` was exact-match only, narrower than the plan's binding
"match the agent side" requirement. Read `lazyclaw/budgets/resolver.py:
resolve_project` (used by `lazyclaw/skills/builtin/budget_manager.py:349`)
and mirrored its exact tier order and single-hit-only rule in a new file,
`mobile/lib/core/project_resolver.dart` (`resolveProjectMatch`):

- **exact** → normalized name == normalized query, single hit resolves.
- **substring** → normalized query is contained in normalized name, single
  hit resolves.
- **fuzzy** → similarity ratio >= 0.85 (same threshold the Python resolver
  uses), single hit resolves.
- More than one hit at ANY tier stops resolution right there (ambiguous,
  never falls through to a looser tier) — mirrors the Python resolver's
  `"multi"` reason short-circuiting. Zero hits at a tier falls through to the
  next; zero hits everywhere (or an empty query) resolves to null.

**Fuzzy metric used, and why it's a faithful stand-in:** Dart has no
built-in equivalent of `difflib.SequenceMatcher.ratio()` (Ratcliff/
Obershelp), and porting that specific algorithm wasn't the actual
requirement — what the single-hit-only gating needs is "how close are these
two short strings," not a particular formula. Used a normalized Levenshtein
(edit-distance) ratio instead: `1 - editDistance / max(len(a), len(b))`, a
standard, well-understood similarity measure. Verified by hand for the test
fixtures (e.g. `"clubbay"` vs `"clubbey"`: one substitution / 7 chars =
ratio ≈ 0.857, clears 0.85) rather than assumed.

`add_expense_sheet.dart`'s three call sites (`_onDescriptionChanged`,
`_applyProjectSuggestion`, the strip's `showSuggestions` gate) now call
`resolveProjectMatch(token, _projects)` instead of the old exact-only helper,
which was deleted.

**New tests:**
- `mobile/test/core/project_resolver_test.dart` (10 tests) — exact,
  substring single/ambiguous, fuzzy single/ambiguous/distant, no-match,
  empty-query, empty-list, and tier precedence (exact wins before substring
  even runs).
- `mobile/test/screens/add_expense_sheet_test.dart` — 3 new widget tests
  pinning the exact scenarios from review: `"Clubbay VIP"` + `#clubbay`
  auto-applies (no strip); two projects both containing `"club"` + `#club`
  → strip shown (ambiguous, no silent guess — note: didn't assert the
  dropdown's value against a specific project id here, since with no
  `initialProjectId` the picker's own default-first-project seed already
  lands on `p1` for unrelated reasons; the real signal is the strip
  appearing); no match at all → strip shown.

### 2. `ParsedExpense.amount`'s docstring corrected (Important)

The doc claimed a line with no amount "simply parses to an all-null
result" — false: `projectTokenPattern.firstMatch(masked)` runs
UNCONDITIONALLY (`masked` falls back to the raw input when there's no
amount token), so `parseSmartExpense('coffee #cafe')` returns
`project: 'cafe'` with `amount: null`. Kept the runtime behavior (it's the
better UX — `#project` can pre-fill while the user is still typing the
amount) and rewrote both the library-level doc comment and the
`ParsedExpense.amount` field doc to describe it accurately: "mandatory
anchor" refers to the MASKING ORDER (amount is found and masked first, so
its span and the project's span can never overlap), not a gate that skips
project detection. Added a pinning test: `coffee #cafe` → `amount: null`,
`project: 'cafe'`, `cleanDescription: 'coffee'`.

### 3. Minors

- (a) `removeProjectToken` now reuses a cached top-level `_whitespace`
  RegExp in `smart_add/project_token.dart` instead of compiling a fresh one
  per call (it runs on every keystroke of a smart-add field).
- (b) The masking test in `smart_add_expense_parser_test.dart` now checks
  `t.kind == SmartTokenKind.amount`/`.project` directly (imported
  `SmartTokenKind` from `smart_add_parser.dart`) instead of
  `t.kind.toString().contains('amount')`.
- (c) Extracted `_ExpenseProjectSuggestionStrip` to its own file,
  `mobile/lib/screens/expenses/expense_project_suggestion_strip.dart`
  (renamed public `ExpenseProjectSuggestionStrip` since it now crosses a
  file boundary) — a clean, self-contained lift with no shared private
  state. `add_expense_sheet.dart` is now 501 lines (was 595).

### Gates re-run after all of the above

- `flutter test test/core/ test/screens/` → **1071 tests, 1 failure** — the
  same documented pre-existing `expenses_range_filter_test.dart` failure
  (unrelated file, untouched). `home_screen_test.dart` did not fail.
- `flutter analyze` → **65 issues**, matching baseline exactly, zero new
  (confirmed by grepping the full issue list for every file this round
  touched or added — no hits).
- All new/changed test files individually re-run and green:
  `project_resolver_test.dart` (10/10), `smart_add_expense_parser_test.dart`
  (18/18), `add_expense_sheet_test.dart` (8/8).

### Still true from round 1, unaffected by these fixes

- Concurrent-agent boundary respected: `dates.dart`/`times.dart`/
  `priority.dart`/`recurrence_patterns.dart` and their tests untouched this
  round either (verified via `git status` before committing — only the
  files listed above are modified/new).
- Currency still not surfaced in the UI (unchanged, still out of v1 scope).
