import type { Owner } from "./noteColors";
import { FILTER_CATEGORIES, OWNER_META, SKILLS_VAULT_KEYS } from "./noteColors";
import { CategoryIcon, OWNER_ICONS } from "./icons";
import { Infinity as InfinityIcon, Wrench } from "lucide-react";

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
  // Total shape count across all kind/shape* chips → drives the toggle's
  // count badge so the user knows whether opening the vault reveals
  // anything at all.
  let shapeCount = 0;
  for (const k of SKILLS_VAULT_KEYS) shapeCount += counts[k] ?? 0;
  return (
    <div className="px-3 py-2 border-b border-border space-y-2">
      {/* Owner tabs + Skills vault toggle */}
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
            <Wrench size={12} strokeWidth={1.75} color={skillsVaultOpen ? "#22c55e" : undefined} />
            <span>Skills</span>
            <span className="opacity-60 tabular-nums">{shapeCount}</span>
          </button>
        )}
      </div>

      {/* Category chips */}
      <div className="flex flex-wrap gap-1">
        {FILTER_CATEGORIES.map((c) => {
          const isShapeChip = SKILLS_VAULT_KEYS.has(c.key);
          // Hide shape chips entirely when the vault is closed — they
          // re-appear the moment the user opens it.
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
              <CategoryIcon keyName={c.key} size={12} color={hidden ? undefined : c.ring} />
              {count >= 2 && <span className="opacity-70 tabular-nums">{count}</span>}
            </button>
          );
        })}
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
