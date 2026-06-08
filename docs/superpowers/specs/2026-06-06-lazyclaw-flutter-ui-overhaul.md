# LazyClaw Flutter — UI/UX Overhaul + Full Control — Design & Plan

**Date:** 2026-06-06
**Status:** Draft for review
**Branch:** `feat/flutter-mobile` (current: v1.5.2, 5 functional tabs, all 3 data domains offline-first)

**Direction (locked with user):**
- **Design language:** Premium dark, refined — elevate the existing dark + amber-claw brand to a polished product (depth, spacing, typographic hierarchy, tasteful motion). NOT a from-scratch aesthetic.
- **Scope:** "Both, phased" — Phase 1 polishes + fully wires the existing surfaces; Phase 2 adds the web app's power surfaces (skills, vault, jobs, watchers, MCP, audit, memory).
- **Structure:** Keep bottom-tab nav, **add a Home dashboard** as the front door.

**Guiding principle:** A **shared design-system kit (`lib/ui/`) is built FIRST and every screen consumes it.** This is what keeps a parallel-agent rebuild visually coherent. No screen hard-codes colors, spacing, or text styles — they all reference the tokens + components.

---

## 1. Design System (`lib/ui/`) — the foundation

### 1.1 Color tokens (`lib/ui/tokens/colors.dart` → `AppColors`)
Dark, layered surfaces (deeper base than today for contrast), amber-claw accent.

| Token | Value | Use |
|---|---|---|
| `bgBase` | `#0B0B12` | app background |
| `bgSurface` | `#14141F` | cards, sheets |
| `bgSurfaceElevated` | `#1C1C2B` | raised cards, app bar |
| `bgSurfaceHover` | `#24243A` | pressed/hover |
| `borderSubtle` | `#FFFFFF0F` (6%) | hairlines |
| `borderDefault` | `#FFFFFF1A` (10%) | dividers, input borders |
| `textPrimary` | `#F5F5FA` | headings/body |
| `textSecondary` | `#A8A8B8` | secondary |
| `textMuted` | `#6E6E80` | captions, placeholders |
| `accent` | `#FF8A3D` | primary actions, active |
| `accentHover` | `#FF9F5C` | pressed accent |
| `accentGradient` | `#FF9F45 → #FF6B35` (135°) | hero highlights, the claw motif |
| `onAccent` | `#1A1208` | text/icons on amber |
| `success` `warn` `error` `info` | `#3DD68C` `#FFB020` `#FF5C5C` `#5AA9FF` | semantic |

Budget bar traffic-light reuses `success`/`warn`/`error`. Keep the existing per-status task/priority colors but remap to these tokens.

### 1.2 Typography (`lib/ui/tokens/typography.dart` → `AppText`)
Add `google_fonts` and use **Inter** (premium, legible). Scale (size/weight/letter-spacing):
- `display` 30 / 700 / -0.5 · `headline` 24 / 700 / -0.3 · `titleL` 20 / 600 · `title` 17 / 600 · `bodyL` 16 / 400 · `body` 14 / 400 / 0.1 · `label` 13 / 600 · `caption` 11.5 / 500 / 0.3
- Line-heights ~1.35 body, ~1.2 headings. All colors via `AppColors`.

### 1.3 Spacing / radii / motion
- `AppSpacing`: `xs 4, sm 8, md 12, lg 16, xl 24, xxl 32, xxxl 48` (4-pt system). All paddings/gaps use these.
- `AppRadii`: `sm 8, md 12, lg 16, xl 20, pill 999`.
- `AppElevation`: subtle shadow `rgba(0,0,0,0.35)` blur 16 y6 + a 1px `borderSubtle` on cards (depth without heaviness).
- `AppMotion`: `fast 150ms`, `base 220ms`, `emphasized 320ms`, curve `easeOutCubic`; page transition = fade-through; list-insert = fade+slide 8px; loading = shimmer skeletons; success = subtle scale/check.

### 1.4 Theme (`lib/ui/app_theme.dart`)
Replace the harvested `core/theme/app_theme.dart` with a token-driven `ThemeData` (Material 3, dark). Single source; delete the old per-accent map. Wire Inter via `google_fonts`. Keep `buildTheme()` name/signature so `main.dart` needs no change (or update the one call site).

### 1.5 Component kit (`lib/ui/components/`)
Every screen uses these — no bespoke one-offs:
- `LzScaffold`, `LzAppBar` (large/standard, optional gradient title)
- `LzButton` (primary filled-amber, secondary tonal, ghost, danger) + `LzIconButton`
- `LzCard` (elevated surface + borderSubtle), `LzSection` (header + content), `LzListTile`
- `LzTextField`, `LzSearchField` (with clear)
- `LzChip` (filter/choice), `LzBadge`, `LzStatusDot`, `LzPill`
- `LzBanner` (offline / info / error — replaces ad-hoc banners), `LzEmptyState` (icon + title + hint + optional action), `LzSkeleton` (shimmer list/card placeholders)
- `LzBottomSheet`, `LzDialog`, `LzConfirm`
- `LzBottomNav` (custom: animated active indicator, the 6 destinations), `LzAvatar`, `LzProgressBar` (budget/sync), `LzSyncBadge` (cloud-off / syncing / synced)
- `LzRefresh` (themed pull-to-refresh)

**Acceptance for the kit:** a `lib/ui/` gallery/demo widget + widget tests render every component in light of the tokens; `flutter analyze` clean.

---

## 2. Navigation + Home dashboard

Bottom nav becomes **6 destinations**: **Home · Chat · Tasks · Money · Notes** + a **Settings** entry (app-bar action or a 6th tab). Keep `StatefulShellRoute.indexedStack` (preserves chat WS + provider state across tabs).

### 2.1 Home dashboard (`lib/screens/home_screen.dart`, NEW)
The front door — read-only glanceable summary + quick actions. Sections (all `LzCard`/`LzSection`, all data from existing providers):
```
┌─────────────────────────────────────────┐
│  Good evening                    [⟳ sync]│   header: greeting + sync status pill
│  ─────────────────────────────────────── │
│  TODAY                                    │   tasks due today + overdue (top 3),
│  ☐ Call James · overdue           [chip] │   tap → Tasks tab / detail
│  ☐ Ship invoice · 6pm                     │
│                                  See all →│
│  ─────────────────────────────────────── │
│  THIS MONTH                  €420 / €600  │   budget snapshot: total spend vs budget,
│  ▓▓▓▓▓▓▓▓▓▓▓▓░░░░  70%  amber bar         │   traffic-light bar, tap → Money tab
│  ─────────────────────────────────────── │
│  RECENT CHAT                              │   last assistant message preview,
│  "I've drafted the proposal…"     open →  │   tap → Chat tab
│  ─────────────────────────────────────── │
│  [ + Task ] [ + Expense ] [ + Note ] [💬] │   quick-action row → opens add sheets/chat
└─────────────────────────────────────────┘
```
Pull-to-refresh syncs all domains. Offline banner at top when unreachable.

---

## 3. Screen redesigns (Phase 1 — rebuild each with the kit)

Each keeps its current functionality + offline behavior; only the presentation + wiring change. All consume `lib/ui/`.

- **Chat** (`chat_screen.dart`): refined message bubbles (asymmetric radius, sender spacing), polished tool-call chips + background-task result cards + plan/approval cards using `LzCard`; a proper input bar (multiline grow, send affordance, connection state in app bar); empty state; smooth autoscroll + streaming cursor.
- **Tasks** (`tasks_screen.dart`): sectioned list (Overdue / Today / Upcoming / Done) with `LzSection`; priority `LzChip`, due chips, swipe-to-complete + swipe-to-delete, `LzSyncBadge` on dirty; a polished add sheet (title + quick due/priority); `LzEmptyState`; shimmer while loading.
- **Money/Expenses** (`expenses_screen.dart`): hero budget summary (this-month total + traffic-light `LzProgressBar`), project cards with per-project bars, expense ledger grouped by date, add-expense sheet (amount keypad-friendly, project picker, vendor), new-project flow; offline badges.
- **Notes** (`notes_screen.dart`): card-style list with title + preview + pinned indicator + tag chips; refined search field; the detail/edit view gets a premium markdown reader + clean edit mode; `LzEmptyState`; offline badges. Search stays local.
- **Settings** (`settings_screen.dart`) — see §4.

**Per-screen acceptance:** uses only `lib/ui/` components/tokens (no hard-coded colors/sizes), keeps existing provider wiring + offline behavior, `flutter analyze` clean, existing tests pass (update widget tests as needed).

---

## 4. Full control + wiring (Phase 1 — Settings becomes a real control center)

`settings_screen.dart` reorganized into `LzSection`s; **wire every control to real state** (no dead toggles):

1. **Account** — username/display name, **Log out**, app version + "Check for update" (hits `/api/mobile/version`).
2. **Server** — gateway URL editor (existing), connection test (ping `/api/system/about`), reachability indicator.
3. **Models / Brain** — **ECO mode switch** (HYBRID / FULL / CLAUDE / MINIMAX) + brain/worker model display. Wire to the backend model-settings endpoint (web app already exposes this — reuse `/api/settings` or the model-config route; if no mobile-friendly GET/SET exists, add a minimal one). This is the headline "full control" item.
4. **Notifications** — toggles per kind (chat reply, background-task done, approvals); persist in `users.settings` via `/api/settings/general` (or a notifications route). Reflect on the device notification permission.
5. **Sync** — "Sync now" (runs all 3 engines), background-sync interval (Off/15m/30m/1h), "View conflicts" (lists the local `conflicts` table — surfaces the `create_rejected`/LWW entries), "Clear local cache", "Wipe on logout" toggle.
6. **Permissions** — allow/ask/deny per skill category via `/api/permissions` (read + toggle). Read-only acceptable for v1 if write is heavy; prefer toggles.
7. **Android** (HyperOS helper) — deep-links to Autostart + battery-unrestricted (the §9 guidance from the original spec), shown on Android only.
8. **About** — version/build/sha, links.

**Wiring acceptance:** each control round-trips real state (GET on open, SET persists + reflects after reopen); ECO-mode + notifications + sync controls verified against the running gateway. Add the minimal backend endpoints only where a mobile GET/SET doesn't already exist (additive, tested, needs `make rebuild`).

---

## 5. Phase 2 (later — the web's power surfaces)

New screens behind a **"More"** entry (a grid/list reachable from Home or Settings), each reusing `lib/ui/`:
- **Skills** (browse/enable, run instruction skills), **Vault** (encrypted secrets — view/add/delete), **Jobs** (cron list + next-run), **Watchers** (URL monitors + history), **MCP** (servers + status), **Audit** (action log), **Memory** (agent memory view/edit). Each maps to an existing `/api/*` route. Scoped + planned separately once Phase 1 ships.

---

## 6. Execution phasing (how the agents build it)

**Phase A — Design system foundation (FIRST, mostly sequential — everything depends on it).**
Build `lib/ui/` (tokens + theme + component kit + Inter font), the new `LzBottomNav`, and wire the 6-destination shell + the empty `HomeScreen` route into `app_router.dart`/`main.dart`. Verify the app still builds + runs with the new theme (old screens temporarily using the new theme is fine). This is the shared contract; it cannot be parallelized with the screens. One agent (review the kit before screens consume it).

**Phase B — Screen redesigns (PARALLEL after A — one agent per screen, all consume `lib/ui/`).**
Disjoint files: `home_screen.dart` (new), `chat_screen.dart`, `tasks_screen.dart`, `expenses_screen.dart`, `notes_screen.dart`, `settings_screen.dart`(+wiring). The shared kit is read-only for these agents; the router/nav was finalized in Phase A so screen agents don't touch it. Fan out 6 agents. Review the redesigned screens for consistency (do they actually use the kit?) before merge.

**Phase A.5 — backend control endpoints (PARALLEL with B, Python, disjoint).**
Any GET/SET the Settings control center needs that doesn't exist yet (ECO mode, notification prefs, permissions read/toggle for mobile). Mirrors the existing sync-backend pattern; needs `make rebuild`.

**Phase C — Phase-2 power surfaces (separate effort, later).**

Each phase ends with: `flutter analyze` clean, `flutter test` green, an APK published (version bump), a coherence review.

---

## 7. Risks / decisions

- **Coherence is the #1 risk** with parallel screen agents → mitigated by the Phase-A kit being built + reviewed FIRST, and a per-screen acceptance rule "uses only `lib/ui/`."
- **Adding `google_fonts`** pulls Inter at build (cached) — acceptable; alternative is bundling the .ttf (smaller, offline-safe). Default: bundle Inter `.ttf` assets to avoid runtime fetch.
- **Don't regress offline/sync** — screen redesigns must keep the existing provider wiring + offline badges + banners; tests guard this.
- **ECO-mode control** depends on a backend GET/SET — verify the web's endpoint is mobile-usable before promising the toggle; otherwise add a minimal route (Phase A.5).
- **Scope honesty:** Phase 1 (kit + 6 screens + wiring) is itself large (~7-8 agents across 2 phases). Phase 2 power surfaces are explicitly deferred.

---

## 8. Out of scope (Phase 1)
Phase-2 power surfaces (skills/vault/jobs/watchers/MCP/audit/memory), iOS polish, FCM push, light theme, tablet layouts.
