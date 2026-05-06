/** Single source of truth for LazyBrain icons.
 *
 *  Two icon systems live side-by-side:
 *
 *  1. **Lucide-react components** — used inside the `<svg>` graph canvas
 *     (`GraphView.tsx` node badges). Phosphor font icons cannot render
 *     inside `<svg>` so the graph stays on Lucide. `CategoryIcon` and
 *     `CATEGORY_ICONS` serve this in-canvas use.
 *
 *  2. **Phosphor web-font glyphs** — used everywhere else (sidebars,
 *     filter chips, header buttons, modals, callouts). One ~30KB woff2
 *     covers all ~70 icon names we use. `HtmlCategoryIcon` /
 *     `CATEGORY_ICONS_HTML` and the named `Plus` / `Search` / `User` /
 *     etc. exports below all render via `PhosphorIcon`.
 *
 *  The named exports keep the same call-site shape as the previous
 *  Lucide imports (`<Plus size={16} />`) so consumer files don't need
 *  to change their JSX — only their import paths (or nothing, if they
 *  already import from this file). */
import {
  AlarmClock as LuAlarmClock,
  AlertTriangle as LuAlertTriangle,
  Ban as LuBan,
  BarChart3 as LuBarChart3,
  BookOpen as LuBookOpen,
  Bookmark as LuBookmark,
  Brain as LuBrain,
  Briefcase as LuBriefcase,
  Calendar as LuCalendar,
  ChefHat as LuChefHat,
  Check as LuCheck,
  Contact as LuContact,
  Database as LuDatabase,
  Diamond as LuDiamond,
  Download as LuDownload,
  FileText as LuFileText,
  Globe as LuGlobe,
  Hourglass as LuHourglass,
  Layers as LuLayers,
  Lightbulb as LuLightbulb,
  Link2 as LuLink2,
  ListTodo as LuListTodo,
  Paperclip as LuPaperclip,
  Sparkles as LuSparkles,
  Star as LuStar,
  Terminal as LuTerminal,
  Wrench as LuWrench,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { CSSProperties, SVGProps } from "react";
import { PhosphorIcon, type PhosphorIconProps } from "./PhosphorIcon";

// ── Graph-canvas (Lucide, renders inside <svg>) ────────────────────────

/** Pick a Lucide component by category key. Used by GraphView node
 *  badges (rendered inside the SVG canvas). Falls back to FileText. */
export const CATEGORY_ICONS: Record<string, LucideIcon> = {
  task: LuListTodo,
  journal: LuBookOpen,
  lesson: LuLightbulb,
  shape: LuWrench,
  "shape-pending": LuHourglass,
  "shape-failed": LuAlertTriangle,
  "shape-known-bad": LuBan,
  til: LuBrain,
  decision: LuCheck,
  price: LuBarChart3,
  deadline: LuAlarmClock,
  command: LuTerminal,
  recipe: LuChefHat,
  contact: LuContact,
  idea: LuSparkles,
  reference: LuLink2,
  rollup: LuBarChart3,
  layer: LuLayers,
  imported: LuDownload,
  pinned: LuStar,
  auto: LuSparkles,
  memory: LuDatabase,
  "site-memory": LuGlobe,
  "daily-log": LuCalendar,
  survival: LuBriefcase,
  fact: LuDiamond,
  learned_preference: LuBookmark,
  context: LuPaperclip,
};

export const DEFAULT_CATEGORY_ICON: LucideIcon = LuFileText;

/** SVG-context category icon — renders Lucide inside the graph canvas. */
export function CategoryIcon({
  keyName,
  size = 14,
  color,
  className,
  strokeWidth = 1.75,
}: {
  keyName: string;
  size?: number;
  color?: string;
  className?: string;
  strokeWidth?: number;
}) {
  const Icon = CATEGORY_ICONS[keyName] ?? DEFAULT_CATEGORY_ICON;
  return (
    <Icon
      size={size}
      color={color}
      strokeWidth={strokeWidth}
      className={className}
      aria-hidden
    />
  );
}

// ── HTML-context (Phosphor web font) ────────────────────────────────────

/** Pick a Phosphor glyph name by category key. Used by HtmlCategoryIcon
 *  and any HTML surface (sidebars, filter chips). Falls back to "file-text". */
export const CATEGORY_ICONS_HTML: Record<string, string> = {
  task: "list-checks",
  journal: "book-open",
  lesson: "lightbulb",
  shape: "wrench",
  "shape-pending": "hourglass",
  "shape-failed": "warning",
  "shape-known-bad": "prohibit",
  til: "brain",
  decision: "check",
  price: "chart-bar",
  deadline: "alarm",
  command: "terminal-window",
  recipe: "chef-hat",
  contact: "address-book",
  idea: "sparkle",
  reference: "link",
  rollup: "chart-bar",
  layer: "stack",
  imported: "download-simple",
  pinned: "star",
  auto: "sparkle",
  memory: "database",
  "site-memory": "globe",
  "daily-log": "calendar",
  survival: "briefcase",
  fact: "diamond",
  learned_preference: "bookmark-simple",
  context: "paperclip",
};

export const DEFAULT_CATEGORY_ICON_NAME = "file-text";

/** Phosphor unicode codepoints — used by graph-canvas node badges where
 *  the icon must render INSIDE an `<svg>`. We render one `<text>` element
 *  with `font-family: "Phosphor"` and the icon's codepoint as content;
 *  the browser rasterises the glyph from the woff2 we already load for
 *  the HTML icons, so there's no extra font download. One SVG element
 *  per node instead of the 5-10 `<path>` elements that Lucide components
 *  emit — same visual, ~6× lighter DOM. Codepoints sourced from the
 *  Phosphor regular `selection.json`. */
export const CATEGORY_CODEPOINTS: Record<string, string> = {
  task: "\ueadc",            // list-checks
  journal: "\ue0e6",         // book-open
  lesson: "\ue2dc",          // lightbulb
  shape: "\ue5d4",           // wrench
  "shape-pending": "\ue2b2", // hourglass
  "shape-failed": "\ue4e0",  // warning
  "shape-known-bad": "\ue3de", // prohibit
  til: "\ue74e",             // brain
  decision: "\ue182",        // check
  price: "\ue150",           // chart-bar
  deadline: "\ue006",        // alarm
  command: "\ueae8",         // terminal-window
  recipe: "\ued8e",          // chef-hat
  contact: "\ue6f8",         // address-book
  idea: "\ue6a2",            // sparkle
  reference: "\ue2e2",       // link
  rollup: "\ue150",          // chart-bar
  layer: "\ue466",           // stack
  imported: "\ue20c",        // download-simple
  pinned: "\ue46a",          // star
  auto: "\ue6a2",            // sparkle
  memory: "\ue1de",          // database
  "site-memory": "\ue288",   // globe
  "daily-log": "\ue108",     // calendar
  survival: "\ue0ee",        // briefcase
  fact: "\ue1ec",            // diamond
  learned_preference: "\ue0ea", // bookmark-simple
  context: "\ue39a",         // paperclip
};

export const DEFAULT_CATEGORY_CODEPOINT = "\ue23a"; // file-text

/** HTML-context category icon — Phosphor font glyph. Use this in sidebars,
 *  filter chips, headers, anywhere outside the graph canvas. */
export function HtmlCategoryIcon({
  keyName,
  size = 14,
  color,
  className,
}: {
  keyName: string;
  size?: number;
  color?: string;
  className?: string;
}) {
  const name = CATEGORY_ICONS_HTML[keyName] ?? DEFAULT_CATEGORY_ICON_NAME;
  return (
    <PhosphorIcon name={name} size={size} color={color} className={className} />
  );
}

// ── Owner icons (HTML context) ─────────────────────────────────────────

type OwnerIconProps = Omit<PhosphorIconProps, "name">;

/** Owner badge icons — Phosphor wrappers, drop-in replacements for the
 *  previous lucide User / Bot / FileText components. Cast to LucideIcon
 *  for call-site type compatibility — at runtime each entry is a plain
 *  function component that React renders identically. */
export const OWNER_ICONS = {
  user: ((p: OwnerIconProps) => (
    <PhosphorIcon name="user" {...p} />
  )) as unknown as LucideIcon,
  agent: ((p: OwnerIconProps) => (
    <PhosphorIcon name="robot" {...p} />
  )) as unknown as LucideIcon,
  unknown: ((p: OwnerIconProps) => (
    <PhosphorIcon name="file-text" {...p} />
  )) as unknown as LucideIcon,
} as const;

// ── Drop-in Phosphor wrappers (replace Lucide imports site-wide) ───────

/** Common props shared by every Phosphor wrapper. Mirrors the subset of
 *  the Lucide API that consumer call sites actually use, so swapping the
 *  import path is the only change required. */
export type IconProps = {
  size?: number;
  color?: string;
  className?: string;
  style?: CSSProperties;
  /** Accepted for compatibility — Phosphor regular has fixed stroke width. */
  strokeWidth?: number;
  /** Accepted for compatibility — see PhosphorIcon notes. */
  fill?: string;
  title?: string;
};

const ph = (name: string): LucideIcon => {
  const Component = (p: IconProps) => <PhosphorIcon name={name} {...p} />;
  Component.displayName = `Ph(${name})`;
  // Cast: at the type level we masquerade as LucideIcon so existing
  // typed call sites (`Icon: LucideIcon`, `Record<string, LucideIcon>`)
  // accept these wrappers without code changes. At runtime React just
  // sees a function component and renders it normally.
  return Component as unknown as LucideIcon;
};

// Action icons
export const Plus = ph("plus");
export const Minus = ph("minus");
export const Search = ph("magnifying-glass");
export const ZoomIn = ph("magnifying-glass-plus");
export const X = ph("x");
export const Check_ = ph("check"); // exported below as `Check`
export const ExternalLink = ph("arrow-square-out");
export const Pencil = ph("pencil-simple");
export const Trash2 = ph("trash");
export const Pin = ph("push-pin");
export const PinOff = ph("push-pin-slash");
export const Save = ph("floppy-disk");
export const Download = ph("download-simple");
export const Settings2 = ph("gear");
export const Archive = ph("archive");
export const RotateCcw = ph("arrow-counter-clockwise");
export const RefreshCw = ph("arrows-clockwise");
export const Move = ph("arrows-out-cardinal");
export const Maximize2 = ph("arrows-out");
export const Filter = ph("funnel");

// Disclosure / cursor
export const ChevronRight = ph("caret-right");
export const ChevronDown = ph("caret-down");
export const ChevronUp = ph("caret-up");
export const MousePointer2 = ph("cursor");

// Layout / panels
export const PanelLeftOpen = ph("sidebar-simple");
export const PanelLeftClose = ph("sidebar-simple");
export const PanelRightOpen = ph("sidebar");
export const PanelRightClose = ph("sidebar");
export const Layout = ph("layout");
export const Command = ph("command");

// Content / data
export const Brain_ph = ph("brain");
export const Bot = ph("robot");
export const User = ph("user");
export const FileText_ph = ph("file-text");
export const BookOpen_ph = ph("book-open");
export const Calendar_ph = ph("calendar");
export const Clock = ph("clock");
export const AlarmClock_ph = ph("alarm");
export const Hash = ph("hash");
export const Network = ph("graph");
export const Link2_ph = ph("link");
export const Layers_ph = ph("stack");
export const ListTodo_ph = ph("list-checks");
export const Tag = ph("tag");
export const StickyNote = ph("note");
export const ListTree = ph("tree-view");
export const MessageSquare = ph("chat");
export const Zap = ph("lightning");
export const Sparkles_ph = ph("sparkle");
export const Lightbulb_ph = ph("lightbulb");
export const Wrench_ph = ph("wrench");
export const Lock = ph("lock");
export const Film = ph("film-strip");
export const CheckSquare = ph("check-square");

// Status
export const Info = ph("info");
export const CheckCircle2 = ph("check-circle");
export const AlertTriangle_ph = ph("warning");
export const Flame = ph("flame");
export const XCircle = ph("x-circle");
export const HelpCircle = ph("question");
export const Quote = ph("quote");
export const Bug = ph("bug");
export const Infinity = ph("infinity");

// Re-export Phosphor names that collided with the Lucide imports above
// under the canonical site-wide name. The local Lucide imports (used
// only by CATEGORY_ICONS / CategoryIcon for the graph canvas) keep
// their original identifiers via the `Lu*` aliases or by being scoped
// to internal use.
export {
  Check_ as Check,
  Brain_ph as Brain,
  FileText_ph as FileText,
  BookOpen_ph as BookOpen,
  Calendar_ph as Calendar,
  AlarmClock_ph as AlarmClock,
  Link2_ph as Link2,
  Layers_ph as Layers,
  ListTodo_ph as ListTodo,
  Sparkles_ph as Sparkles,
  Lightbulb_ph as Lightbulb,
  Wrench_ph as Wrench,
  AlertTriangle_ph as AlertTriangle,
};

/** Filled gold pinned-marker — kept on Lucide because it relies on the
 *  `fill` prop and a specific outline-with-fill render. Phosphor's regular
 *  weight is outline only. Importing the fill weight just for this one
 *  icon would add ~30KB without payoff. Re-exported as LucideIcon (the
 *  underlying type) for call-site compatibility with existing typed
 *  consumers. */
export const Star: LucideIcon = LuStar;

export type IconComponent = LucideIcon;

/** Convenience typed props for inline SVG Lucide-styled icons. */
export type LineIconProps = Omit<SVGProps<SVGSVGElement>, "size"> & {
  size?: number;
};

// Export PhosphorIcon for direct use when a name isn't in the wrappers.
export { PhosphorIcon };
