import { useState } from "react";
import type { Owner } from "./noteColors";
import { FILTER_CATEGORIES, OWNER_META, SKILLS_VAULT_KEYS } from "./noteColors";
import { CategoryIcon, OWNER_ICONS } from "./icons";
import {
  Infinity as InfinityIcon,
  Wrench,
  ChevronRight,
  Filter as FilterIcon,
} from "lucide-react";

interface Props {
  hiddenCategories: Set<string>;
  ownerFilter: Owner | "all";
  onToggleCategory: (key: string) => void;
  onSetOwner: (o: Owner | "all") => void;
  counts: Record<string, number>;
  ownerCounts: Record<Owner, number>;
  /** True when the user has the Skills vault toggle on. Persisted by the
   *  parent (see `useSkillsVault`). When off (default), all
   *  `kind/shape*` chips are added to `hiddenCategories` and the toggle
   *  button is dimmed. */
  skillsVaultOpen: boolean;
  onToggleSkillsVault: () => void;
}

const LS_FILTERBAR_OPEN = "lazybrain-filterbar-open";

export function FilterBar({
  hiddenCategories,
  ownerFilter,
  onToggleCategory,
  onSetOwner,
  counts,
  ownerCounts,
  skillsVaultOpen,
  onToggleSkillsVault,
}: Props) {
  const allCount = ownerCounts.user + ownerCounts.agent + ownerCounts.unknown;
  // Total shape count across all kind/shape* chips → drives the Skills
  // toggle's count badge so the user knows whether opening the vault
  // reveals anything at all.
  let shapeCount = 0;
  for (const k of SKILLS_VAULT_KEYS) shapeCount += counts[k] ?? 0;

  // Disclosure state — the chip grid + Skills vault button collapse
  // behind a single compact row by default. The category chip list
  // would otherwise consume ~67% of the sidebar viewport before any
  // note row even appears.
  const [filterBarOpen, setFilterBarOpen] = useState<boolean>(() => {
    try {
      return localStorage.getItem(LS_FILTERBAR_OPEN) === "true";
    } catch {
      return false;
    }
  });
  const toggleFilterBar = () => {
    setFilterBarOpen((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(LS_FILTERBAR_OPEN, next ? "true" : "false");
      } catch {
        // Private mode / disabled storage — value just doesn't persist.
      }
      return next;
    });
  };

  // Visible-chip + hidden-chip counts — both filtered by the same
  // zero-count + skills-vault rules the chip grid uses below, so the
  // numbers in the disclosure match exactly what the user would see if
  // they expanded the section.
  let visibleChipCount = 0;
  let hiddenChipCount = 0;
  for (const c of FILTER_CATEGORIES) {
    if (SKILLS_VAULT_KEYS.has(c.key) && !skillsVaultOpen) continue;
    if ((counts[c.key] ?? 0) === 0) continue;
    visibleChipCount++;
    if (hiddenCategories.has(c.key)) hiddenChipCount++;
  }

  return (
    <div className="px-3 py-2 border-b border-border space-y-2">
      {/* Owner tabs — always visible (highest-frequency control) */}
      <div className="flex items-center gap-1">
        <OwnerTab
          label="All"
          active={ownerFilter === "all"}
          Icon={InfinityIcon}
          count={allCount}
          ring="#64748b"
          onClick={() => onSetOwner("all")}
        />
        <OwnerTab
          label={OWNER_META.user.label}
          active={ownerFilter === "user"}
          Icon={OWNER_ICONS.user}
          count={ownerCounts.user}
          ring={OWNER_META.user.ring}
          onClick={() => onSetOwner("user")}
        />
        <OwnerTab
          label={OWNER_META.agent.label}
          active={ownerFilter === "agent"}
          Icon={OWNER_ICONS.agent}
          count={ownerCounts.agent}
          ring={OWNER_META.agent.ring}
          onClick={() => onSetOwner("agent")}
        />
      </div>

      {/* Filter disclosure — chevron + label + counts. Visually rhymes
          with SidebarSection headers in PageListSidebar so the bar feels
          like part of the same vertical rhythm rather than a separate
          control panel. */}
      <div>
        <button
          onClick={toggleFilterBar}
          className="w-full flex items-center gap-1.5 px-1 py-0.5 rounded hover:bg-bg-hover transition-colors group/fb"
          title={filterBarOpen ? "Hide filters" : "Show filters"}
        >
          <ChevronRight
            size={10}
            strokeWidth={2.2}
            className={`text-text-muted/70 transition-transform ${
              filterBarOpen ? "rotate-90" : ""
            }`}
          />
          <FilterIcon size={11} strokeWidth={1.75} className="text-text-muted" />
          <span className="text-[10px] uppercase tracking-wider text-text-muted group-hover/fb:text-text-secondary">
            Filters
          </span>
          <span className="text-[10px] tabular-nums text-text-muted/60">
            {visibleChipCount}
          </span>
          {!filterBarOpen && hiddenChipCount > 0 && (
            <span
              className="ml-auto flex items-center gap-1 text-[10px] tabular-nums"
              style={{ color: "var(--color-accent)" }}
              title={`${hiddenChipCount} categor${
                hiddenChipCount === 1 ? "y" : "ies"
              } hidden — expand to manage`}
            >
              <span
                className="w-1 h-1 rounded-full"
                style={{ background: "var(--color-accent)" }}
              />
              <span>{hiddenChipCount} hidden</span>
            </span>
          )}
        </button>

        {filterBarOpen && (
          <div className="mt-2 space-y-2">
            {/* Skills vault toggle — only meaningful when there are
                shapes to reveal. Lives inside the expanded panel so the
                collapsed state stays a single line. */}
            {shapeCount > 0 && (
              <button
                onClick={onToggleSkillsVault}
                className={`flex items-center gap-1 px-2 py-1 rounded text-[11px] transition-colors ${
                  skillsVaultOpen
                    ? "bg-bg-primary text-text-primary"
                    : "text-text-muted hover:text-text-primary hover:bg-bg-hover opacity-70"
                }`}
                title={
                  skillsVaultOpen
                    ? "Skills vault open — agent's known shapes are visible. Click to hide."
                    : "Skills vault hidden — click to reveal verified / pending / failed agent shapes."
                }
              >
                <Wrench
                  size={12}
                  strokeWidth={1.75}
                  color={skillsVaultOpen ? "#22c55e" : undefined}
                />
                <span>Skills</span>
                <span className="opacity-60 tabular-nums">{shapeCount}</span>
              </button>
            )}

            {/* Category chips */}
            <div className="flex flex-wrap gap-1">
              {FILTER_CATEGORIES.map((c) => {
                const isShapeChip = SKILLS_VAULT_KEYS.has(c.key);
                // Hide shape chips entirely when the vault is closed —
                // they re-appear the moment the user opens it.
                if (isShapeChip && !skillsVaultOpen) return null;
                const hidden = hiddenCategories.has(c.key);
                const count = counts[c.key] ?? 0;
                if (count === 0) return null;
                return (
                  <button
                    key={c.key}
                    onClick={() => onToggleCategory(c.key)}
                    className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] transition-all ${
                      hidden
                        ? "bg-bg-primary text-text-muted opacity-50 hover:opacity-80"
                        : "bg-bg-hover text-text-secondary hover:text-text-primary"
                    }`}
                    style={hidden ? undefined : { borderLeft: `2px solid ${c.ring}` }}
                    title={`${c.label} (${count}) — ${hidden ? "hidden" : "visible"}`}
                  >
                    <CategoryIcon
                      keyName={c.key}
                      size={12}
                      color={hidden ? undefined : c.ring}
                    />
                    {count >= 2 && (
                      <span className="opacity-70 tabular-nums">{count}</span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}


import type { LucideIcon } from "lucide-react";

function OwnerTab({
  label,
  active,
  Icon,
  count,
  ring,
  onClick,
}: {
  label: string;
  active: boolean;
  Icon: LucideIcon;
  count: number;
  ring: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex-1 flex items-center justify-center gap-1.5 px-2 py-1 rounded text-[11px] transition-colors ${
        active
          ? "bg-bg-primary text-text-primary"
          : "text-text-muted hover:text-text-primary hover:bg-bg-hover"
      }`}
      style={active ? { boxShadow: `inset 0 -2px 0 ${ring}` } : undefined}
    >
      <Icon size={12} strokeWidth={1.75} color={active ? ring : undefined} />
      <span>{label}</span>
      <span className="opacity-60 tabular-nums">{count}</span>
    </button>
  );
}
