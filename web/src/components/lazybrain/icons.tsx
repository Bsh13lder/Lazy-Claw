/** Single source of truth for LazyBrain icons — Lucide, line-style,
 *  consistent stroke width. No emoji. */
import {
  AlarmClock,
  Archive,
  BarChart3,
  Bot,
  BookOpen,
  Brain,
  Briefcase,
  Bookmark,
  Check,
  Calendar,
  ChefHat,
  Clock,
  Contact,
  Database,
  Diamond,
  Download,
  ExternalLink,
  FileText,
  Globe,
  Hash,
  Lightbulb,
  Layers,
  Link2,
  ListTodo,
  Network,
  Paperclip,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Save,
  Search,
  Settings2,
  Sparkles,
  Star,
  Terminal,
  Trash2,
  User,
  X,
  Film,
  Lock,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { SVGProps } from "react";

/** Pick an icon by category key. Falls back to `FileText` for unknown. */
export const CATEGORY_ICONS: Record<string, LucideIcon> = {
  task:        ListTodo,
  journal:     BookOpen,
  lesson:      Lightbulb,
  til:         Brain,
  decision:    Check,
  price:       BarChart3,
  deadline:    AlarmClock,
  command:     Terminal,
  recipe:      ChefHat,
  contact:     Contact,
  idea:        Sparkles,
  reference:   Link2,
  rollup:      BarChart3,
  layer:       Layers,
  imported:    Download,
  pinned:      Star,
  auto:        Sparkles,
  memory:      Database,
  "site-memory": Globe,
  "daily-log": Calendar,
  survival:    Briefcase,
  fact:        Diamond,
  learned_preference: Bookmark,
  context:     Paperclip,
};

export const DEFAULT_CATEGORY_ICON: LucideIcon = FileText;

/** Small helper — renders a category icon at a given size + color. */
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

/** Owner icons. */
export const OWNER_ICONS = { user: User, agent: Bot, unknown: FileText } as const;

export type IconComponent = LucideIcon;

/** Re-export handy action icons for sidebars/editors. */
export {
  Plus,
  Search,
  Network,
  ExternalLink,
  Pencil,
  Trash2,
  Star,
  Pin,
  PinOff,
  X,
  Check,
  Film,
  Lock,
  Settings2,
  User,
  Bot,
  Brain,
  Calendar,
  BookOpen,
  Clock,
  Hash,
  Save,
  Archive,
  AlarmClock,
};

/** Convenience typed props for inline SVG Lucide-styled icons. */
export type LineIconProps = Omit<SVGProps<SVGSVGElement>, "size"> & { size?: number };
