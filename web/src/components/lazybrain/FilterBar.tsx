import type { Owner } from "./noteColors";
import { FILTER_CATEGORIES, OWNER_META } from "./noteColors";

interface Props {
  hiddenCategories: Set<string>;
  ownerFilter: Owner | "all";
  onToggleCategory: (key: string) => void;
  onSetOwner: (o: Owner | "all") => void;
  counts: Record<string, number>;
  ownerCounts: Record<Owner, number>;
}

export function FilterBar({
  hiddenCategories,
  ownerFilter,
  onToggleCategory,
  onSetOwner,
  counts,
  ownerCounts,
}: Props) {
  return (
    <div className="px-3 py-2 border-b border-border space-y-2">
      {/* Owner tabs */}
      <div className="flex items-center gap-1">
        <OwnerTab
          label={`All`}
          active={ownerFilter === "all"}
          emoji="∞"
          count={ownerCounts.user + ownerCounts.agent + ownerCounts.unknown}
          ring="#64748b"
          onClick={() => onSetOwner("all")}
        />
        <OwnerTab
          label={OWNER_META.user.label}
          active={ownerFilter === "user"}
          emoji={OWNER_META.user.emoji}
          count={ownerCounts.user}
          ring={OWNER_META.user.ring}
          onClick={() => onSetOwner("user")}
        />
        <OwnerTab
          label={OWNER_META.agent.label}
          active={ownerFilter === "agent"}
          emoji={OWNER_META.agent.emoji}
          count={ownerCounts.agent}
          ring={OWNER_META.agent.ring}
          onClick={() => onSetOwner("agent")}
        />
      </div>

      {/* Category chips */}
      <div className="flex flex-wrap gap-1">
        {FILTER_CATEGORIES.map((c) => {
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
              title={`${c.label} — ${hidden ? "hidden" : "visible"}`}
            >
              <span>{c.emoji}</span>
              <span className="opacity-70">{count}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}


function OwnerTab({
  label,
  active,
  emoji,
  count,
  ring,
  onClick,
}: {
  label: string;
  active: boolean;
  emoji: string;
  count: number;
  ring: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex-1 flex items-center justify-center gap-1 px-2 py-1 rounded text-[11px] transition-colors ${
        active
          ? "bg-bg-primary text-text-primary"
          : "text-text-muted hover:text-text-primary hover:bg-bg-hover"
      }`}
      style={active ? { boxShadow: `inset 0 -2px 0 ${ring}` } : undefined}
    >
      <span>{emoji}</span>
      <span>{label}</span>
      <span className="opacity-60">{count}</span>
    </button>
  );
}
