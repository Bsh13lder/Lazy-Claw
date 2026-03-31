import { useState, useEffect, useCallback, useRef } from "react";

const COLORS = {
  bg: "#1a1b26",
  panel: "#24283b",
  border: "#6B7280",
  header: "#F59E0B",
  active: "#14B8A6",
  success: "#84CC16",
  error: "#F87171",
  thinking: "#FBBF24",
  idle: "#9CA3AF",
  cost: "#34D399",
  specialist: "#A78BFA",
  text: "#c0caf5",
  dimText: "#565f89",
  blue: "#7aa2f7",
  orange: "#ff9e64",
  green: "#9ece6a",
  red: "#f7768e",
  purple: "#bb9af7",
  cyan: "#7dcfff",
};

/* ─── INITIAL DATA ─── */
const INITIAL_REQUESTS = [
  {
    id: 1, msg: "search for python jobs on upwork", phase: "thinking", model: "haiku-3.5",
    steps: 3, maxSteps: 7, stepName: "analyzing job listings", tools: "web_search, browser",
    specialists: [{ name: "researcher", status: "running" }, { name: "browser", status: "queued" }],
    tokens: { in: "4.2K", out: "1.1K" }, cost: "0.003", startedAt: "14:32:08", duration: "24s",
    bg: false, compact: false,
  },
  {
    id: 2, msg: "check my gmail for invoices", phase: "tool", model: "nanbeige-3B",
    steps: 2, maxSteps: null, stepName: "reading inbox", tools: "browser",
    specialists: null, tokens: { in: "1.8K", out: "0.4K" }, cost: "0.000",
    startedAt: "14:32:24", duration: "8s", bg: true, compact: false,
  },
  {
    id: 3, msg: "what time is it in tokyo?", phase: "done", model: "nanbeige-3B",
    steps: 1, maxSteps: 1, stepName: "get_time", tools: null, specialists: null,
    tokens: { in: "0.3K", out: "0.1K" }, cost: "0.000", startedAt: "14:31:55",
    finishedAt: "14:31:56", duration: "1.2s", bg: false, compact: true,
  },
  {
    id: 4, msg: "calculate 15% tip on $84", phase: "done", model: "nanbeige-3B",
    steps: 1, maxSteps: 1, stepName: "calculate", tools: null, specialists: null,
    tokens: { in: "0.2K", out: "0.1K" }, cost: "0.000", startedAt: "14:30:12",
    finishedAt: "14:30:13", duration: "0.8s", bg: false, compact: true,
  },
  {
    id: 5, msg: "remind me at 5pm to call mom", phase: "done", model: "nanbeige-3B",
    steps: 1, maxSteps: 1, stepName: "set_reminder", tools: null, specialists: null,
    tokens: { in: "0.4K", out: "0.2K" }, cost: "0.000", startedAt: "14:29:40",
    finishedAt: "14:29:41", duration: "1.1s", bg: false, compact: true,
  },
];

const INITIAL_BG_TASKS = [
  { id: "bg1", name: "monitor prices", status: "running", duration: "45.2s", detail: "reading page" },
  { id: "bg2", name: "daily summary", status: "done", ago: "2m ago" },
  { id: "bg3", name: "check watchers", status: "done", ago: "5m ago" },
];

const INITIAL_SETTINGS = {
  ecoMode: "eco", brainModel: "haiku-3.5", workerModel: "nanbeige-3B",
  fallbackModel: "gpt-5-mini", showBadges: true, monthlyBudget: 5.0,
  teamMode: true, criticMode: "auto", autoDelegate: true,
  maxSpecialists: 5, maxRam: 2048, specialistTimeout: 300,
  browser: "Brave", headless: true, humanDelays: true, maxTabs: 5, cdpPort: 9222,
  defaultRule: "ask", browserActions: "allow", shellCommands: "ask",
  fileAccess: "allow", vaultAccess: "deny",
  survivalMode: false, minRate: 25, maxConcurrent: 2,
  telegram: true, discord: false, whatsapp: false,
};

const ECO_MODES = ["eco", "hybrid", "off"];
const BRAIN_MODELS = ["haiku-3.5", "sonnet-4", "gpt-5-mini"];
const WORKER_MODELS = ["nanbeige-3B", "qwen-4B", "haiku-3.5"];
const FALLBACK_MODELS = ["gpt-5-mini", "sonnet-4", "haiku-3.5"];
const CRITIC_MODES = ["auto", "always", "never"];
const BROWSERS = ["Brave", "Chrome", "Chromium"];
const PERM_OPTIONS = ["allow", "ask", "deny"];

/* ─── PRIMITIVES ─── */
const Mono = ({ children, color, bold, dim, style }) => (
  <span
    style={{
      fontFamily: "'JetBrains Mono', 'Fira Code', 'SF Mono', monospace",
      color: dim ? COLORS.dimText : color || COLORS.text,
      fontWeight: bold ? "bold" : "normal",
      fontSize: "11.5px",
      lineHeight: "1.45",
      ...style,
    }}
  >
    {children}
  </span>
);

const Panel = ({ title, children, maxHeight, borderColor, style }) => (
  <div
    style={{
      border: `1px solid ${borderColor || COLORS.border}`,
      borderRadius: "4px",
      margin: "2px 4px",
      overflow: "hidden",
      maxHeight: maxHeight,
      ...style,
    }}
  >
    {title && (
      <div
        style={{
          borderBottom: `1px solid ${borderColor || COLORS.border}`,
          padding: "2px 8px",
          background: COLORS.panel,
        }}
      >
        <Mono color={COLORS.header} bold>
          {title}
        </Mono>
      </div>
    )}
    <div style={{ padding: "4px 8px", background: COLORS.bg }}>{children}</div>
  </div>
);

const ProgressBar = ({ value, max, width = 120, color = COLORS.active }) => (
  <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
    <span
      style={{
        display: "inline-block",
        width,
        height: 8,
        background: "#2a2e3f",
        borderRadius: 2,
        overflow: "hidden",
      }}
    >
      <span
        style={{
          display: "block",
          width: `${(value / max) * 100}%`,
          height: "100%",
          background: color,
          borderRadius: 2,
        }}
      />
    </span>
  </span>
);

const Badge = ({ text, color }) => (
  <span
    style={{
      fontFamily: "'JetBrains Mono', monospace",
      fontSize: "10px",
      padding: "1px 6px",
      borderRadius: "3px",
      background: color + "22",
      color: color,
      fontWeight: "bold",
      marginRight: 4,
    }}
  >
    {text}
  </span>
);

const Toggle = ({ on, label, onClick }) => (
  <span
    style={{ display: "inline-flex", alignItems: "center", gap: 5, marginRight: 10, cursor: onClick ? "pointer" : "default" }}
    onClick={onClick}
  >
    <span
      style={{
        display: "inline-block",
        width: 28,
        height: 14,
        borderRadius: 7,
        background: on ? COLORS.green + "55" : "#2a2e3f",
        position: "relative",
        border: `1px solid ${on ? COLORS.green : COLORS.border}`,
        transition: "background 0.2s, border-color 0.2s",
      }}
    >
      <span
        style={{
          display: "block",
          width: 10,
          height: 10,
          borderRadius: "50%",
          background: on ? COLORS.green : COLORS.idle,
          position: "absolute",
          top: 1,
          left: on ? 15 : 2,
          transition: "left 0.2s, background 0.2s",
        }}
      />
    </span>
    <Mono dim={!on} color={on ? COLORS.text : undefined} style={{ fontSize: 11 }}>
      {label || (on ? "on" : "off")}
    </Mono>
  </span>
);

const SettingRow = ({ label, children }) => (
  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "2px 0", minHeight: 22 }}>
    <Mono dim style={{ fontSize: 11, minWidth: 110 }}>{label}</Mono>
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>{children}</div>
  </div>
);

const SettingSelect = ({ value, options, color, onChange }) => {
  const handleClick = () => {
    if (!options || !onChange) return;
    const idx = options.indexOf(value);
    const next = options[(idx + 1) % options.length];
    onChange(next);
  };
  const permColor = (v) => {
    if (v === "allow") return COLORS.green;
    if (v === "deny") return COLORS.red;
    if (v === "ask") return COLORS.header;
    return color || COLORS.active;
  };
  const c = options === PERM_OPTIONS ? permColor(value) : (color || COLORS.active);
  return (
    <span
      onClick={handleClick}
      style={{
        fontFamily: "monospace",
        fontSize: 11,
        padding: "1px 8px",
        borderRadius: 3,
        background: c + "22",
        color: c,
        border: `1px solid ${c + "44"}`,
        cursor: options ? "pointer" : "default",
        transition: "background 0.15s, color 0.15s, border-color 0.15s",
        userSelect: "none",
      }}
    >
      {value} {options ? "▾" : ""}
    </span>
  );
};

/* ─── TOAST ─── */
function Toast({ message, type }) {
  if (!message) return null;
  const color = type === "error" ? COLORS.red : type === "success" ? COLORS.green : COLORS.active;
  return (
    <div
      style={{
        position: "fixed",
        top: 12,
        right: 16,
        padding: "6px 14px",
        background: COLORS.panel,
        border: `1px solid ${color}`,
        borderRadius: 6,
        zIndex: 100,
        animation: "fadeIn 0.15s ease-out",
      }}
    >
      <Mono color={color} bold style={{ fontSize: 11 }}>{message}</Mono>
    </div>
  );
}

/* ─── SYSTEM BAR ─── */
function SystemBar({ settings, requests }) {
  const activeCount = requests.filter((r) => r.phase !== "done" && r.phase !== "error" && r.phase !== "cancelled").length;
  const cancelledCount = requests.filter((r) => r.phase === "cancelled").length;
  const ecoLabel = settings.ecoMode === "eco" ? "ECO" : settings.ecoMode === "hybrid" ? "HYBRID" : "FULL";
  const ecoColor = settings.ecoMode === "eco" ? COLORS.green : settings.ecoMode === "hybrid" ? COLORS.orange : COLORS.cyan;
  return (
    <Panel borderColor={COLORS.header}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}>
        <Badge text={ecoLabel} color={ecoColor} />
        <Mono dim>Brain:</Mono>
        <Mono color={COLORS.cyan}>{settings.brainModel}</Mono>
        <Mono dim>Worker:</Mono>
        <Mono color={COLORS.cyan}>{settings.workerModel}</Mono>
        <Mono dim>RAM:</Mono>
        <Mono color={COLORS.green}>62%</Mono>
        <Mono dim>Cost:</Mono>
        <Mono color={COLORS.cost}>$0.012</Mono>
        <Mono dim>Tokens:</Mono>
        <Mono color={COLORS.blue}>↑14.2K ↓8.7K</Mono>
        <Mono dim>Active:</Mono>
        <Mono color={activeCount > 0 ? COLORS.active : COLORS.idle}>{activeCount}</Mono>
        {cancelledCount > 0 && (
          <>
            <Mono dim>Cancelled:</Mono>
            <Mono color={COLORS.red}>{cancelledCount}</Mono>
          </>
        )}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", marginTop: 2 }}>
        <Mono color={settings.telegram ? COLORS.green : COLORS.idle}>{settings.telegram ? "✓" : "✗"} Telegram</Mono>
        <Mono color={COLORS.green}>✓ Brave CDP</Mono>
        <Mono dim>MCP: 4</Mono>
        <Mono dim>Queue: 0</Mono>
        <Mono color={COLORS.green}>Free: 4.2 GB</Mono>
      </div>
    </Panel>
  );
}

/* ─── REQUEST CARD ─── */
function RequestCard({ id, msg, phase, model, steps, maxSteps, stepName, tools, specialists, tokens, cost, startedAt, finishedAt, duration, bg: isBg, compact, focused, onCancel, onClick }) {
  const [flashRed, setFlashRed] = useState(false);
  const [hovered, setHovered] = useState(false);

  const phaseConfig = {
    thinking: { icon: "●", color: COLORS.orange },
    tool: { icon: "◆", color: COLORS.blue },
    done: { icon: "✓", color: COLORS.green },
    error: { icon: "✗", color: COLORS.red },
    stuck: { icon: "⚠", color: COLORS.red },
    queued: { icon: "○", color: COLORS.idle },
    streaming: { icon: "●", color: COLORS.cyan },
    cancelled: { icon: "✗", color: COLORS.red },
  };
  const p = phaseConfig[phase] || phaseConfig.thinking;
  const isCancelled = phase === "cancelled";
  const isTerminal = isCancelled || phase === "done" || phase === "error";
  const canCancel = !isTerminal && onCancel;

  const borderColor = flashRed
    ? COLORS.red
    : isCancelled
    ? COLORS.dimText
    : phase === "done"
    ? COLORS.green
    : phase === "error"
    ? COLORS.red
    : focused
    ? COLORS.header
    : COLORS.active;

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        border: `1px solid ${borderColor}`,
        borderRadius: "6px",
        padding: "8px 10px",
        background: isCancelled ? COLORS.bg : COLORS.panel,
        flex: compact ? "1 1 30%" : "1 1 45%",
        minWidth: compact ? 180 : 260,
        minHeight: compact ? 90 : 130,
        display: "flex",
        flexDirection: "column",
        gap: 3,
        opacity: isCancelled ? 0.5 : 1,
        transition: "border-color 0.2s, opacity 0.3s, background 0.2s",
        position: "relative",
        cursor: onClick ? "pointer" : "default",
        outline: focused ? `1px solid ${COLORS.header}` : "none",
        outlineOffset: 1,
      }}
    >
      {/* Cancel button — hover or focused */}
      {canCancel && (hovered || focused) && (
        <span
          onClick={(e) => {
            e.stopPropagation();
            setFlashRed(true);
            setTimeout(() => setFlashRed(false), 400);
            onCancel(id);
          }}
          style={{
            position: "absolute",
            top: 4,
            right: 6,
            fontFamily: "monospace",
            fontSize: 13,
            color: COLORS.red,
            cursor: "pointer",
            padding: "0 4px",
            borderRadius: 3,
            background: COLORS.red + "22",
            lineHeight: "16px",
            userSelect: "none",
          }}
          title="Cancel this task (x)"
        >
          ×
        </span>
      )}

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Mono bold style={{ fontSize: 11 }}>
          <Mono color={COLORS.dimText}>#{id}</Mono>{" "}
          <Mono
            color={isCancelled ? COLORS.dimText : COLORS.text}
            style={isCancelled ? { textDecoration: "line-through", fontSize: 11 } : { fontSize: 11 }}
          >
            {msg.length > (compact ? 22 : 35) ? msg.slice(0, compact ? 22 : 35) + "…" : `"${msg}"`}
          </Mono>
        </Mono>
        <span style={{ display: "flex", gap: 4 }}>
          {isBg && <Badge text="bg" color={COLORS.purple} />}
          {isCancelled && <Badge text="cancelled" color={COLORS.red} />}
        </span>
      </div>

      {/* Phase + Model + Step */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <Mono color={p.color} style={{ fontSize: 11 }}>
          {p.icon} {phase}
        </Mono>
        <Mono color={COLORS.cyan} style={{ fontSize: 10 }}>{model}</Mono>
        <Mono dim style={{ fontSize: 10 }}>
          step {steps}{maxSteps ? `/${maxSteps}` : ""}
        </Mono>
        {maxSteps && <ProgressBar value={steps} max={maxSteps} width={compact ? 50 : 70} color={isCancelled ? COLORS.dimText : borderColor} />}
      </div>

      {/* Step name */}
      {stepName && (
        <Mono color={isCancelled ? COLORS.dimText : COLORS.text} style={{ fontSize: 10 }}>↳ "{stepName}"</Mono>
      )}

      {/* Tools */}
      {tools && !compact && (
        <Mono dim style={{ fontSize: 10 }}>
          Tools: <Mono color={COLORS.blue} style={{ fontSize: 10 }}>{tools}</Mono>
        </Mono>
      )}

      {/* Specialists */}
      {specialists && !compact && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {specialists.map((s, i) => {
            const sColor = s.status === "done" ? COLORS.green : s.status === "error" ? COLORS.red : s.status === "cancelled" ? COLORS.dimText : s.status === "running" ? COLORS.active : COLORS.idle;
            const sIcon = s.status === "done" ? "✓" : s.status === "error" ? "✗" : s.status === "cancelled" ? "✗" : s.status === "running" ? "◎" : "○";
            return (
              <Mono key={i} color={sColor} style={{ fontSize: 10 }}>
                {sIcon} {s.name}
              </Mono>
            );
          })}
        </div>
      )}

      {/* Footer: tokens + time */}
      <div style={{ marginTop: "auto", display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", paddingTop: 3, borderTop: `1px solid ${COLORS.border}33` }}>
        <Mono color={COLORS.blue} style={{ fontSize: 10 }}>↑{tokens?.in || "0"} ↓{tokens?.out || "0"}</Mono>
        <Mono color={COLORS.cost} style={{ fontSize: 10 }}>${cost}</Mono>
        <Mono dim style={{ fontSize: 10 }}>│</Mono>
        <Mono dim style={{ fontSize: 10 }}>{startedAt}</Mono>
        {finishedAt && <Mono dim style={{ fontSize: 10 }}>→ {finishedAt}</Mono>}
        <Mono color={isCancelled ? COLORS.red : phase === "done" ? COLORS.green : COLORS.active} bold style={{ fontSize: 10 }}>{duration}</Mono>
      </div>
    </div>
  );
}

/* ─── ACTIVITY PANEL ─── */
function ActivityPanel({ requests, focusedCard, onCancel, onFocusCard }) {
  // Sort: active first, cancelled last
  const sorted = [...requests].sort((a, b) => {
    const order = { thinking: 0, streaming: 0, tool: 1, queued: 2, stuck: 3, done: 4, error: 5, cancelled: 6 };
    return (order[a.phase] ?? 3) - (order[b.phase] ?? 3);
  });
  return (
    <Panel title="Activity" style={{ flex: 1 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {sorted.map((r) => (
          <RequestCard
            key={r.id}
            {...r}
            focused={focusedCard === r.id}
            onCancel={onCancel}
            onClick={() => onFocusCard(r.id)}
          />
        ))}
      </div>
    </Panel>
  );
}

/* ─── BACKGROUND TASKS ─── */
function TeamLeadBar({ bgTasks, onCancelBg }) {
  const running = bgTasks.filter((t) => t.status === "running");
  const done = bgTasks.filter((t) => t.status === "done");
  const cancelled = bgTasks.filter((t) => t.status === "cancelled");
  return (
    <Panel title="Background Tasks">
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        {running.map((t) => (
          <div key={t.id} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <Mono color={COLORS.active}>◎ </Mono>
            <Mono>{t.name}</Mono>
            <Mono dim> — bg — {t.duration} — </Mono>
            <Mono color={COLORS.blue}>{t.detail}</Mono>
            <span
              onClick={() => onCancelBg(t.id)}
              style={{
                fontFamily: "monospace", fontSize: 11, color: COLORS.red,
                cursor: "pointer", padding: "0 4px", borderRadius: 3,
                background: COLORS.red + "22", marginLeft: 4, userSelect: "none",
              }}
            >
              ×
            </span>
          </div>
        ))}
        {running.length === 0 && <Mono dim>No active background tasks</Mono>}
      </div>
      <div style={{ marginTop: 4, display: "flex", gap: 16, flexWrap: "wrap" }}>
        {done.map((t) => (
          <Mono key={t.id} dim>✓ {t.name} — {t.ago}</Mono>
        ))}
        {cancelled.map((t) => (
          <Mono key={t.id} color={COLORS.red} style={{ fontSize: 11 }}>✗ {t.name} — cancelled</Mono>
        ))}
      </div>
    </Panel>
  );
}

/* ─── JOBS & WATCHERS ─── */
function JobsBar() {
  return (
    <Panel title="Jobs & Watchers">
      <div style={{ display: "flex", gap: 20, flexWrap: "wrap", marginBottom: 4 }}>
        <Mono>
          <Mono color={COLORS.orange}>🕐 </Mono>
          <Mono bold>price-check</Mono>
          <Mono dim> — every 30m — last: 12m ago — next: 18m</Mono>
        </Mono>
        <Mono>
          <Mono color={COLORS.orange}>🕐 </Mono>
          <Mono bold>daily-report</Mono>
          <Mono dim> — daily 9:00 — last: 5h ago — next: 19h</Mono>
        </Mono>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
          <Mono color={COLORS.active}>◎ </Mono>
          <Mono bold>whatsapp</Mono>
          <Mono dim>— every 5m — last: 2m ago — next: 3m —</Mono>
          <Mono color={COLORS.dimText} style={{ fontStyle: "italic" }}>"check new messages from boss"</Mono>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
          <Mono color={COLORS.active}>◎ </Mono>
          <Mono bold>gmail</Mono>
          <Mono dim>— every 15m — last: 8m ago — next: 7m —</Mono>
          <Mono color={COLORS.dimText} style={{ fontStyle: "italic" }}>"check for new invoices or payment confirmations"</Mono>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
          <Mono color={COLORS.green}>◎ </Mono>
          <Mono bold>amazon-price</Mono>
          <Mono dim>— every 1h — last: 34m ago — next: 26m —</Mono>
          <Mono color={COLORS.dimText} style={{ fontStyle: "italic" }}>"check if MacBook Pro price dropped below $1800"</Mono>
        </div>
      </div>
    </Panel>
  );
}

/* ─── LOG PANEL ─── */
function LogPanel({ logs }) {
  const [filter, setFilter] = useState("all");
  const filtered = filter === "all"
    ? logs
    : filter === "tools"
    ? logs.filter((l) => l.icon === "◆" || l.icon === "✓")
    : filter === "errors"
    ? logs.filter((l) => l.icon === "✗" || l.type === "error")
    : filter === "llm"
    ? logs.filter((l) => l.icon === "●")
    : logs;

  return (
    <Panel title="Logs" maxHeight={200} style={{ flex: 1 }}>
      <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
        {["all", "tools", "errors", "llm"].map((f) => (
          <span
            key={f}
            onClick={() => setFilter(f)}
            style={{
              fontFamily: "monospace",
              fontSize: 10,
              padding: "1px 8px",
              borderRadius: 3,
              background: filter === f ? COLORS.active + "33" : "transparent",
              color: filter === f ? COLORS.active : COLORS.dimText,
              border: `1px solid ${filter === f ? COLORS.active : COLORS.border}`,
              cursor: "pointer",
            }}
          >
            {f}
          </span>
        ))}
      </div>
      {filtered.map((l, i) => (
        <div key={i} style={{ marginBottom: 1 }}>
          <Mono color={l.color} bold={l.style === "bold"}>
            {l.icon}{" "}
          </Mono>
          <Mono color={i >= filtered.length - 3 ? l.color : COLORS.dimText} bold={l.style === "bold"}>
            {l.text}
          </Mono>
        </div>
      ))}
    </Panel>
  );
}

/* ─── COSTS ─── */
function CostBar({ settings }) {
  const models = [
    { name: settings.brainModel, cost: 0.009, pct: 75, color: COLORS.cyan },
    { name: settings.workerModel, cost: 0.0, pct: 0, color: COLORS.green, local: settings.ecoMode !== "off" },
    { name: settings.fallbackModel, cost: 0.003, pct: 25, color: COLORS.orange },
  ];
  return (
    <Panel title="Costs">
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "center" }}>
        {models.map((m) => (
          <div key={m.name} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Mono color={m.color}>{m.name}</Mono>
            {m.local && <Badge text="LOCAL" color={COLORS.green} />}
            <ProgressBar value={m.pct} max={100} width={60} color={m.color} />
            <Mono dim>${m.cost.toFixed(3)}</Mono>
            <Mono dim>({m.pct}%)</Mono>
          </div>
        ))}
        <Mono dim>│</Mono>
        <Mono color={COLORS.cost} bold>Total: $0.012 / ${settings.monthlyBudget.toFixed(2)}</Mono>
      </div>
    </Panel>
  );
}

/* ─── AI ROUTING ─── */
function AIRoutingBar({ settings }) {
  const routes = [
    { icon: "🧠", name: settings.brainModel, tag: "PAID", calls: 14, cost: 0.009 },
    { icon: "🔧", name: settings.workerModel, tag: settings.ecoMode !== "off" ? "LOCAL" : "PAID", calls: 31, cost: 0.0 },
    { icon: "⚡", name: settings.fallbackModel, tag: "PAID", calls: 3, cost: 0.003 },
  ];
  return (
    <Panel title="AI Routing">
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "center" }}>
        {routes.map((r) => (
          <div key={r.name} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <Mono>{r.icon}</Mono>
            <Mono color={COLORS.text}>{r.name}</Mono>
            <Badge text={r.tag} color={r.tag === "LOCAL" ? COLORS.green : COLORS.orange} />
            <Mono dim>{r.calls} calls</Mono>
            <Mono color={COLORS.cost}>${r.cost.toFixed(3)}</Mono>
          </div>
        ))}
        <Mono dim>│</Mono>
        <Mono color={COLORS.green} bold>{settings.ecoMode !== "off" ? "65% local" : "0% local"}</Mono>
        <Mono dim>today</Mono>
      </div>
    </Panel>
  );
}

/* ─── SETTINGS PANEL (interactive) ─── */
function SettingsPanel({ settings, onChange }) {
  const set = (key) => (val) => onChange({ ...settings, [key]: val });
  const toggle = (key) => () => onChange({ ...settings, [key]: !settings[key] });

  const SectionHeader = ({ text }) => (
    <>
      <Mono color={COLORS.header} bold style={{ fontSize: 10, letterSpacing: 1 }}>{text}</Mono>
      <div style={{ borderBottom: `1px solid ${COLORS.border}33`, marginBottom: 4, paddingBottom: 2 }} />
    </>
  );

  return (
    <Panel title="⚙ Settings" borderColor={COLORS.dimText}>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        {/* Column 1: AI / Models */}
        <div style={{ flex: "1 1 200px", minWidth: 200 }}>
          <SectionHeader text="AI / MODELS" />
          <SettingRow label="Mode">
            <SettingSelect value={settings.ecoMode} options={ECO_MODES} color={settings.ecoMode === "eco" ? COLORS.green : settings.ecoMode === "hybrid" ? COLORS.orange : COLORS.cyan} onChange={set("ecoMode")} />
          </SettingRow>
          {settings.ecoMode === "eco" && (
            <>
              <SettingRow label="Brain"><Mono color={COLORS.green}>local only</Mono></SettingRow>
              <SettingRow label="Worker"><Mono color={COLORS.green}>local only</Mono></SettingRow>
              <SettingRow label="Fallback"><Mono color={COLORS.red}>none ($0)</Mono></SettingRow>
            </>
          )}
          {settings.ecoMode === "hybrid" && (
            <>
              <SettingRow label="Brain"><Mono color={COLORS.cyan}>haiku (paid)</Mono></SettingRow>
              <SettingRow label="Worker"><Mono color={COLORS.green}>nanbeige (local)</Mono></SettingRow>
              <SettingRow label="Fallback"><Mono color={COLORS.cyan}>haiku</Mono></SettingRow>
            </>
          )}
          {settings.ecoMode === "full" && (
            <>
              <SettingRow label="Brain">
                <SettingSelect value={settings.brainModel} options={BRAIN_MODELS} color={COLORS.cyan} onChange={set("brainModel")} />
              </SettingRow>
              <SettingRow label="Worker">
                <SettingSelect value={settings.workerModel} options={WORKER_MODELS} color={COLORS.cyan} onChange={set("workerModel")} />
              </SettingRow>
              <SettingRow label="Fallback">
                <SettingSelect value={settings.fallbackModel} options={FALLBACK_MODELS} color={COLORS.orange} onChange={set("fallbackModel")} />
              </SettingRow>
            </>
          )}
          <SettingRow label="Show Badges">
            <Toggle on={settings.showBadges} onClick={toggle("showBadges")} />
          </SettingRow>
          <SettingRow label="Monthly Budget">
            <Mono color={COLORS.cost}>${settings.monthlyBudget.toFixed(2)}</Mono>
          </SettingRow>
        </div>

        {/* Column 2: Teams / Agent */}
        <div style={{ flex: "1 1 200px", minWidth: 200 }}>
          <SectionHeader text="TEAMS / AGENT" />
          <SettingRow label="Team Mode">
            <Toggle on={settings.teamMode} label={settings.teamMode ? "auto" : "off"} onClick={toggle("teamMode")} />
          </SettingRow>
          <SettingRow label="Critic Mode">
            <SettingSelect value={settings.criticMode} options={CRITIC_MODES} color={COLORS.active} onChange={set("criticMode")} />
          </SettingRow>
          <SettingRow label="Auto Delegate">
            <Toggle on={settings.autoDelegate} onClick={toggle("autoDelegate")} />
          </SettingRow>
          <SettingRow label="Max Specialists">
            <Mono color={COLORS.text}>{settings.maxSpecialists}</Mono>
          </SettingRow>
          <SettingRow label="Max RAM (MB)">
            <Mono color={COLORS.text}>{settings.maxRam}</Mono>
          </SettingRow>
          <SettingRow label="Specialist Timeout">
            <Mono color={COLORS.text}>{settings.specialistTimeout}s</Mono>
          </SettingRow>
        </div>

        {/* Column 3: Browser */}
        <div style={{ flex: "1 1 200px", minWidth: 200 }}>
          <SectionHeader text="BROWSER" />
          <SettingRow label="Browser">
            <SettingSelect value={settings.browser} options={BROWSERS} color={COLORS.orange} onChange={set("browser")} />
          </SettingRow>
          <SettingRow label="Headless">
            <Toggle on={settings.headless} onClick={toggle("headless")} />
          </SettingRow>
          <SettingRow label="Human Delays">
            <Toggle on={settings.humanDelays} onClick={toggle("humanDelays")} />
          </SettingRow>
          <SettingRow label="Max Tabs">
            <Mono color={COLORS.text}>{settings.maxTabs}</Mono>
          </SettingRow>
          <SettingRow label="CDP Port">
            <Mono dim>{settings.cdpPort}</Mono>
          </SettingRow>
        </div>

        {/* Column 4: Permissions */}
        <div style={{ flex: "1 1 200px", minWidth: 200 }}>
          <SectionHeader text="PERMISSIONS" />
          <SettingRow label="Default Rule">
            <SettingSelect value={settings.defaultRule} options={PERM_OPTIONS} onChange={set("defaultRule")} />
          </SettingRow>
          <SettingRow label="Browser Actions">
            <SettingSelect value={settings.browserActions} options={PERM_OPTIONS} onChange={set("browserActions")} />
          </SettingRow>
          <SettingRow label="Shell Commands">
            <SettingSelect value={settings.shellCommands} options={PERM_OPTIONS} onChange={set("shellCommands")} />
          </SettingRow>
          <SettingRow label="File Access">
            <SettingSelect value={settings.fileAccess} options={PERM_OPTIONS} onChange={set("fileAccess")} />
          </SettingRow>
          <SettingRow label="Vault Access">
            <SettingSelect value={settings.vaultAccess} options={PERM_OPTIONS} onChange={set("vaultAccess")} />
          </SettingRow>
        </div>

        {/* Column 5: Survival / Channels */}
        <div style={{ flex: "1 1 200px", minWidth: 200 }}>
          <SectionHeader text="SURVIVAL / CHANNELS" />
          <SettingRow label="Survival Mode">
            <Toggle on={settings.survivalMode} label={settings.survivalMode ? "on" : "off"} onClick={toggle("survivalMode")} />
          </SettingRow>
          <SettingRow label="Min Rate ($/hr)">
            <Mono dim>${settings.minRate}</Mono>
          </SettingRow>
          <SettingRow label="Max Concurrent">
            <Mono dim>{settings.maxConcurrent}</Mono>
          </SettingRow>
          <SettingRow label="Telegram">
            <Toggle on={settings.telegram} label={settings.telegram ? "connected" : "off"} onClick={toggle("telegram")} />
          </SettingRow>
          <SettingRow label="Discord">
            <Toggle on={settings.discord} label={settings.discord ? "connected" : "off"} onClick={toggle("discord")} />
          </SettingRow>
          <SettingRow label="WhatsApp">
            <Toggle on={settings.whatsapp} label={settings.whatsapp ? "connected" : "off"} onClick={toggle("whatsapp")} />
          </SettingRow>
        </div>
      </div>
    </Panel>
  );
}

/* ─── ADMIN INPUT ─── */
function AdminInput({ onCommand, inputRef }) {
  const [value, setValue] = useState("");

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && value.trim()) {
      onCommand(value.trim());
      setValue("");
    }
  };

  return (
    <div
      style={{
        margin: "2px 4px",
        padding: "4px 8px",
        background: COLORS.panel,
        border: `1px solid ${COLORS.border}`,
        borderRadius: 4,
        display: "flex",
        alignItems: "center",
        gap: 8,
      }}
    >
      <Mono color={COLORS.header} bold>{"❯"}</Mono>
      <input
        ref={inputRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Type a message or /command..."
        style={{
          flex: 1,
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 12,
          color: COLORS.text,
          background: "transparent",
          border: "none",
          outline: "none",
          caretColor: COLORS.header,
        }}
      />
    </div>
  );
}

/* ─── FOOTER ─── */
function Footer({ showSettings, focusedCard }) {
  const keys = [
    ["q", "Quit"],
    ["/", "Filter"],
    ["Tab", "Navigate"],
    ["1", "Activity"],
    ["2", "Logs"],
    ["3", showSettings ? "← Dashboard" : "Settings"],
    ["x", "Cancel task"],
    ["c", "Copy"],
  ];
  return (
    <div style={{ margin: "0 4px 4px", padding: "2px 8px", display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
      {keys.map(([k, label]) => {
        const isActive = (k === "3" && showSettings) || (k === "x" && focusedCard !== null);
        return (
          <span key={k} style={{ display: "flex", gap: 4, alignItems: "center" }}>
            <span
              style={{
                fontFamily: "monospace",
                fontSize: 10,
                padding: "0 4px",
                background: isActive ? COLORS.active : COLORS.panel,
                border: `1px solid ${isActive ? COLORS.active : COLORS.border}`,
                borderRadius: 3,
                color: isActive ? COLORS.bg : COLORS.header,
              }}
            >
              {k}
            </span>
            <Mono dim={!isActive} color={isActive ? COLORS.active : undefined} style={{ fontSize: 10 }}>{label}</Mono>
          </span>
        );
      })}
    </div>
  );
}

/* ─── MAIN DASHBOARD ─── */
export default function TUIPreview() {
  const [showSettings, setShowSettings] = useState(false);
  const [requests, setRequests] = useState(INITIAL_REQUESTS);
  const [bgTasks, setBgTasks] = useState(INITIAL_BG_TASKS);
  const [settings, setSettings] = useState(INITIAL_SETTINGS);
  const [focusedCard, setFocusedCard] = useState(null);
  const [toast, setToast] = useState(null);
  const inputRef = useRef(null);

  const [logs, setLogs] = useState([
    { icon: ">>", color: COLORS.text, style: "bold", text: 'New request #1: "search for python jobs on upwork"' },
    { icon: "◆", color: COLORS.blue, text: 'tool:web_search(query="python freelance upwork") → 12 results' },
    { icon: "●", color: COLORS.orange, text: "llm:haiku-3.5 ↑2.1K ↓0.8K (1.2s)" },
    { icon: "→", color: COLORS.purple, text: 'delegate → researcher: "analyze top 5 job listings"' },
    { icon: "◆", color: COLORS.blue, text: 'tool:browser(action=read, url="upwork.com/job/123") → 2.4KB' },
    { icon: "✓", color: COLORS.green, text: "result:browser → page loaded, extracted job details" },
    { icon: "●", color: COLORS.orange, text: "llm:nanbeige-3B [LOCAL] ↑1.1K ↓0.3K (0.8s)" },
    { icon: ">>", color: COLORS.text, style: "bold", text: 'New request #2: "check my gmail for invoices"' },
    { icon: "◆", color: COLORS.blue, text: 'tool:browser(action=open, url="mail.google.com") → navigating' },
    { icon: "✓✓", color: COLORS.green, style: "bold", text: "Request #3 completed: \"It's 2:47 AM in Tokyo\" (1.2s, $0.000)" },
    { icon: "✗", color: COLORS.red, text: "error: CDP connection timeout on tab 3 — retrying...", type: "error" },
    { icon: "**", color: COLORS.cyan, text: "info: MCP mcp-freeride reconnected (was offline 12s)" },
  ]);

  const showToast = useCallback((msg, type = "info") => {
    setToast({ message: msg, type });
    setTimeout(() => setToast(null), 2500);
  }, []);

  const addLog = useCallback((icon, color, text, style) => {
    setLogs((prev) => [...prev, { icon, color, text, style }]);
  }, []);

  /* ─── CANCEL LOGIC ─── */
  const cancelRequest = useCallback((id) => {
    setRequests((prev) => prev.map((r) => {
      if (r.id !== id) return r;
      if (r.phase === "done" || r.phase === "error" || r.phase === "cancelled") return r;
      const cancelledSpecs = r.specialists
        ? r.specialists.map((s) => s.status === "running" || s.status === "queued" ? { ...s, status: "cancelled" } : s)
        : null;
      return { ...r, phase: "cancelled", specialists: cancelledSpecs };
    }));
    addLog("✗", COLORS.red, `Cancelled: request #${id} (${requests.find((r) => r.id === id)?.msg || "unknown"})`, "bold");
    showToast(`Cancelled request #${id}`, "success");
    if (focusedCard === id) setFocusedCard(null);
  }, [requests, focusedCard, addLog, showToast]);

  const cancelAllActive = useCallback(() => {
    const active = requests.filter((r) => r.phase !== "done" && r.phase !== "error" && r.phase !== "cancelled");
    if (active.length === 0) { showToast("Nothing to cancel", "info"); return; }
    active.forEach((r) => cancelRequest(r.id));
    showToast(`Cancelled ${active.length} task(s)`, "success");
  }, [requests, cancelRequest, showToast]);

  const cancelBgTask = useCallback((id) => {
    setBgTasks((prev) => prev.map((t) => t.id === id && t.status === "running" ? { ...t, status: "cancelled" } : t));
    const task = bgTasks.find((t) => t.id === id);
    addLog("✗", COLORS.red, `Cancelled bg: ${task?.name || id}`, "bold");
    showToast(`Cancelled bg: ${task?.name || id}`, "success");
  }, [bgTasks, addLog, showToast]);

  const cancelAllBg = useCallback(() => {
    const running = bgTasks.filter((t) => t.status === "running");
    if (running.length === 0) { showToast("No bg tasks to cancel", "info"); return; }
    running.forEach((t) => cancelBgTask(t.id));
  }, [bgTasks, cancelBgTask, showToast]);

  /* ─── COMMAND PARSER ─── */
  const handleCommand = useCallback((cmd) => {
    const parts = cmd.split(/\s+/);
    const command = parts[0].toLowerCase();

    if (command === "/cancel") {
      const arg = parts[1]?.toLowerCase();
      if (!arg || arg === "all") {
        cancelAllActive();
        cancelAllBg();
      } else if (arg === "bg") {
        cancelAllBg();
      } else {
        const id = parseInt(arg, 10);
        if (!isNaN(id)) {
          const found = requests.find((r) => r.id === id);
          if (found) cancelRequest(id);
          else showToast(`Request #${id} not found`, "error");
        } else {
          // Try match by specialist/name
          const match = requests.find((r) =>
            r.specialists?.some((s) => s.name.toLowerCase() === arg && s.status === "running")
          );
          if (match) cancelRequest(match.id);
          else showToast(`No match for "${arg}"`, "error");
        }
      }
    } else if (command === "/set") {
      const key = parts[1]?.toLowerCase();
      const val = parts[2]?.toLowerCase();
      if (key === "eco" && val && ECO_MODES.includes(val)) {
        setSettings((prev) => ({ ...prev, ecoMode: val }));
        addLog("**", COLORS.cyan, `Setting changed: ECO mode → ${val}`, "bold");
        showToast(`ECO mode → ${val}`, "success");
      } else if (key === "brain" && val) {
        const m = BRAIN_MODELS.find((b) => b.toLowerCase().startsWith(val));
        if (m) {
          setSettings((prev) => ({ ...prev, brainModel: m }));
          showToast(`Brain → ${m}`, "success");
        } else showToast(`Unknown brain model: ${val}`, "error");
      } else if (key === "worker" && val) {
        const m = WORKER_MODELS.find((w) => w.toLowerCase().startsWith(val));
        if (m) {
          setSettings((prev) => ({ ...prev, workerModel: m }));
          showToast(`Worker → ${m}`, "success");
        } else showToast(`Unknown worker model: ${val}`, "error");
      } else if (key === "team" && val) {
        setSettings((prev) => ({ ...prev, teamMode: val === "on" || val === "auto" }));
        showToast(`Team mode → ${val}`, "success");
      } else if (key === "budget" && val) {
        const n = parseFloat(val);
        if (!isNaN(n)) {
          setSettings((prev) => ({ ...prev, monthlyBudget: n }));
          showToast(`Budget → $${n.toFixed(2)}`, "success");
        }
      } else {
        showToast(`Usage: /set <eco|brain|worker|team|budget> <value>`, "info");
      }
    } else if (command === "/settings") {
      setShowSettings((prev) => !prev);
    } else {
      addLog(">>", COLORS.text, `"${cmd}"`, "bold");
      showToast(`Sent: "${cmd}"`, "info");
    }
  }, [requests, cancelAllActive, cancelAllBg, cancelRequest, cancelBgTask, addLog, showToast]);

  /* ─── KEYBOARD SHORTCUTS ─── */
  useEffect(() => {
    const handleKey = (e) => {
      // Skip if typing in input
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

      if (e.key === "3") {
        e.preventDefault();
        setShowSettings((prev) => !prev);
      } else if (e.key === "x" && focusedCard !== null && !showSettings) {
        e.preventDefault();
        cancelRequest(focusedCard);
      } else if (e.key === "Tab" && !showSettings) {
        e.preventDefault();
        const ids = requests.map((r) => r.id);
        if (ids.length === 0) return;
        if (focusedCard === null) {
          setFocusedCard(ids[0]);
        } else {
          const idx = ids.indexOf(focusedCard);
          setFocusedCard(ids[(idx + 1) % ids.length]);
        }
      } else if (e.key === "/" || e.key === "1" || e.key === "2") {
        e.preventDefault();
        if (e.key === "/") inputRef.current?.focus();
        if (e.key === "1") setShowSettings(false);
        if (e.key === "2") setShowSettings(false);
      } else if (e.key === "Escape") {
        setFocusedCard(null);
        inputRef.current?.blur();
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [focusedCard, requests, showSettings, cancelRequest]);

  return (
    <div
      style={{
        background: COLORS.bg,
        minHeight: "100vh",
        padding: 4,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Toast message={toast?.message} type={toast?.type} />

      {/* Title */}
      <div style={{ textAlign: "center", padding: "4px 0", borderBottom: `1px solid ${COLORS.border}`, marginBottom: 4 }}>
        <Mono color={COLORS.header} bold style={{ fontSize: 14 }}>🦀 LazyClaw Dashboard</Mono>
        <Mono dim style={{ fontSize: 10, marginLeft: 12 }}>v0.1.0 — uptime 2h 14m — {requests.length} requests processed</Mono>
        {showSettings && (
          <Mono color={COLORS.active} bold style={{ fontSize: 10, marginLeft: 12 }}>[ SETTINGS OPEN ]</Mono>
        )}
      </div>

      <SystemBar settings={settings} requests={requests} />

      {showSettings ? (
        <>
          <SettingsPanel settings={settings} onChange={setSettings} />
          <div style={{ margin: "4px 8px", textAlign: "center" }}>
            <Mono dim style={{ fontSize: 10 }}>Press </Mono>
            <span style={{ fontFamily: "monospace", fontSize: 10, padding: "0 4px", background: COLORS.panel, border: `1px solid ${COLORS.border}`, borderRadius: 3, color: COLORS.header }}>3</span>
            <Mono dim style={{ fontSize: 10 }}> to return to dashboard</Mono>
          </div>
        </>
      ) : (
        <>
          <ActivityPanel
            requests={requests}
            focusedCard={focusedCard}
            onCancel={cancelRequest}
            onFocusCard={setFocusedCard}
          />
          <TeamLeadBar bgTasks={bgTasks} onCancelBg={cancelBgTask} />
          <JobsBar />
          <LogPanel logs={logs} />
          <CostBar settings={settings} />
          <AIRoutingBar settings={settings} />
        </>
      )}

      <AdminInput onCommand={handleCommand} inputRef={inputRef} />
      <Footer showSettings={showSettings} focusedCard={focusedCard} />

      {/* Toggle button for demo */}
      <div
        onClick={() => setShowSettings(!showSettings)}
        style={{
          position: "fixed",
          bottom: 40,
          right: 16,
          padding: "6px 14px",
          background: showSettings ? COLORS.active : COLORS.panel,
          border: `1px solid ${showSettings ? COLORS.active : COLORS.border}`,
          borderRadius: 6,
          cursor: "pointer",
          zIndex: 10,
        }}
      >
        <Mono color={showSettings ? COLORS.bg : COLORS.header} bold style={{ fontSize: 11 }}>
          {showSettings ? "← Back to Dashboard" : "⚙ Open Settings (3)"}
        </Mono>
      </div>
    </div>
  );
}
