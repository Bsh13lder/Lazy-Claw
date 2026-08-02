import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/core/due_date.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/project.dart';
import '../expenses/project_color_picker.dart';

/// Tap-a-chip quick-edit helpers for the Tasks UI.
///
/// Two halves:
///   * **Pure helpers** ([withNewDueDay], [withNewDueTime], [priorityColor],
///     [priorityLabel]) — framework-light, trivially unit-testable.
///   * **Pickers** ([showPriorityMenu], [showProjectPicker],
///     [showThemedDatePicker], [showThemedTimePicker]) — present the quick-edit
///     surfaces and return the chosen value (or null when dismissed).

/// The canonical task priority levels, lowest → highest.
const List<String> kTaskPriorities = ['low', 'medium', 'high', 'urgent'];

/// The kit accent color for a priority level (shared by the row, the menu and
/// the detail sheet so the four levels read identically everywhere).
Color priorityColor(String priority) {
  switch (priority) {
    case 'urgent':
      return AppColors.error;
    case 'high':
      return AppColors.warn;
    case 'medium':
      return AppColors.info;
    default:
      return AppColors.textMuted;
  }
}

/// A capitalized display label for a priority level (`high` → `High`).
String priorityLabel(String priority) {
  if (priority.isEmpty) return priority;
  return priority[0].toUpperCase() + priority.substring(1);
}

// ── Pure due-date recomposition ──────────────────────────────────────────────

/// Recompose a `dueDate` string onto a new calendar [day] while **preserving**
/// any existing time-of-day from [oldDue]. A date-only [oldDue] stays date-only.
String withNewDueDay(String? oldDue, DateTime day) {
  final parts = dueTimeParts(oldDue);
  return composeDueDate(
    DateTime(day.year, day.month, day.day),
    hour: parts?.hour,
    minute: parts?.minute,
  );
}

/// Recompose a `dueDate` string with a new time-of-day while **preserving** the
/// calendar day from [oldDue]. Falls back to today when [oldDue]'s day part
/// can't be parsed.
String withNewDueTime(String oldDue, int hour, int minute) {
  final dayPart = dueDateDayPart(oldDue);
  final parsed = DateTime.tryParse(dayPart);
  final day = parsed != null
      ? DateTime(parsed.year, parsed.month, parsed.day)
      : DateTime.now();
  return composeDueDate(day, hour: hour, minute: minute);
}

// ── Priority popup menu ───────────────────────────────────────────────────────

/// Show a compact popup menu (Low / Medium / High / Urgent) anchored to the
/// chip whose [context] is passed, returning the chosen priority or null when
/// dismissed.
Future<String?> showPriorityMenu(
  BuildContext context, {
  required String current,
}) {
  final box = context.findRenderObject() as RenderBox?;
  final overlay =
      Overlay.of(context).context.findRenderObject() as RenderBox?;

  RelativeRect position;
  if (box != null && overlay != null && box.hasSize) {
    final topLeft = box.localToGlobal(Offset.zero, ancestor: overlay);
    final bottomRight = box.localToGlobal(
      box.size.bottomRight(Offset.zero),
      ancestor: overlay,
    );
    position = RelativeRect.fromRect(
      Rect.fromPoints(topLeft, bottomRight),
      Offset.zero & overlay.size,
    );
  } else {
    position = const RelativeRect.fromLTRB(0, 0, 0, 0);
  }

  return showMenu<String>(
    context: context,
    position: position,
    color: AppColors.bgSurfaceElevated,
    shape: RoundedRectangleBorder(borderRadius: AppRadii.rLg),
    items: [
      for (final p in kTaskPriorities)
        PopupMenuItem<String>(
          key: ValueKey('priority-menu-$p'),
          value: p,
          height: 42,
          child: Row(
            children: [
              Icon(Icons.flag_rounded, size: 16, color: priorityColor(p)),
              const SizedBox(width: AppSpacing.sm),
              Text(
                priorityLabel(p),
                style: AppText.body.copyWith(
                  color:
                      p == current ? priorityColor(p) : AppColors.textPrimary,
                  fontWeight:
                      p == current ? FontWeight.w700 : FontWeight.w500,
                ),
              ),
              if (p == current) ...[
                const Spacer(),
                Icon(Icons.check_rounded, size: 16, color: priorityColor(p)),
              ],
            ],
          ),
        ),
    ],
  );
}

// ── Project picker ────────────────────────────────────────────────────────────

/// The outcome of [showProjectPicker]. A null [category] means "clear the
/// project" (No project); a non-null value is the chosen project name.
/// [createNew] flags that the user tapped the "＋ New project" affordance
/// (only rendered when [showProjectPicker] is called with `allowCreate:
/// true`) — [category] is null in that case and callers should open the
/// project-creation flow instead of treating this as a "clear" result.
class ProjectPickResult {
  const ProjectPickResult(this.category) : createNew = false;
  const ProjectPickResult.createNew()
      : category = null,
        createNew = true;

  final String? category;
  final bool createNew;
}

/// Show a bottom-sheet picker listing [projects] (name + color dot) plus a
/// "No project" option. Returns the chosen [ProjectPickResult], or null when
/// the sheet is dismissed without a choice. [current] (a category string) is
/// highlighted. When [allowCreate] is true, a trailing "＋ New project" row
/// is rendered that pops [ProjectPickResult.createNew].
Future<ProjectPickResult?> showProjectPicker(
  BuildContext context, {
  required List<Project> projects,
  String? current,
  bool allowCreate = false,
}) {
  return LzBottomSheet.show<ProjectPickResult>(
    context,
    title: 'Project',
    builder: (ctx) => _ProjectPickerBody(
      projects: projects,
      current: current,
      allowCreate: allowCreate,
    ),
  );
}

class _ProjectPickerBody extends StatelessWidget {
  const _ProjectPickerBody({
    required this.projects,
    required this.current,
    this.allowCreate = false,
  });

  final List<Project> projects;
  final String? current;
  final bool allowCreate;

  @override
  Widget build(BuildContext context) {
    final cur = (current ?? '').trim().toLowerCase();
    final noProjectSelected = cur.isEmpty;

    return SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // "No project" — clears the category.
          LzListTile(
            key: const Key('project-pick-none'),
            leading: Icon(Icons.block_rounded,
                size: 14, color: AppColors.textMuted),
            title: 'No project',
            trailing: noProjectSelected
                ? const Icon(Icons.check_rounded,
                    size: 18, color: AppColors.accent)
                : null,
            onTap: () =>
                Navigator.of(context).pop(const ProjectPickResult(null)),
          ),
          for (final p in projects)
            LzListTile(
              key: ValueKey('project-pick-${p.id}'),
              leading: ProjectColorDot(hex: p.color, size: 12),
              title: p.name,
              trailing: p.name.toLowerCase() == cur
                  ? const Icon(Icons.check_rounded,
                      size: 18, color: AppColors.accent)
                  : null,
              onTap: () =>
                  Navigator.of(context).pop(ProjectPickResult(p.name)),
            ),
          if (allowCreate)
            LzListTile(
              key: const Key('project-pick-create'),
              leading: Icon(Icons.add_rounded,
                  size: 16, color: AppColors.accent),
              title: '＋ New project',
              titleStyle: AppText.body.copyWith(
                color: AppColors.accent,
                fontWeight: FontWeight.w600,
              ),
              onTap: () => Navigator.of(context)
                  .pop(const ProjectPickResult.createNew()),
            ),
        ],
      ),
    );
  }
}

/// The tappable project control: a color dot + project name (or "No
/// project"), opening the project picker. Shared by the task detail sheet
/// and (via [fieldKey]) the Add Task sheet.
class ProjectChip extends StatelessWidget {
  const ProjectChip({
    super.key,
    required this.projects,
    required this.category,
    required this.onTap,
    required this.fieldKey,
  });

  final List<Project> projects;
  final String? category;
  final VoidCallback onTap;

  /// The [Key] applied to the tappable [InkWell] itself (distinct from
  /// [key], the widget's own key). The detail sheet passes its original
  /// `Key('task-detail-project')` explicitly at its call site so existing
  /// finders keep working; the Add Task sheet passes
  /// `Key('add-task-project')`.
  final Key fieldKey;

  @override
  Widget build(BuildContext context) {
    final hasCategory = category != null && category!.isNotEmpty;
    String? colorHex;
    if (hasCategory) {
      for (final p in projects) {
        if (p.name.toLowerCase() == category!.toLowerCase()) {
          colorHex = p.color;
          break;
        }
      }
    }

    return Material(
      color: Colors.transparent,
      borderRadius: AppRadii.rPill,
      child: InkWell(
        key: fieldKey,
        onTap: onTap,
        borderRadius: AppRadii.rPill,
        child: Container(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.md,
            vertical: AppSpacing.xs + 2,
          ),
          decoration: BoxDecoration(
            color: AppColors.bgSurfaceElevated,
            borderRadius: AppRadii.rPill,
            border: Border.all(color: AppColors.borderDefault),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (hasCategory)
                ProjectColorDot(hex: colorHex, size: 12)
              else
                Icon(
                  Icons.folder_outlined,
                  size: 15,
                  color: AppColors.textMuted,
                ),
              const SizedBox(width: AppSpacing.sm),
              Text(
                hasCategory ? category! : 'No project',
                style: AppText.caption.copyWith(
                  color: hasCategory
                      ? AppColors.textPrimary
                      : AppColors.textMuted,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              Icon(Icons.expand_more, size: 16, color: AppColors.textMuted),
            ],
          ),
        ),
      ),
    );
  }
}

// ── Themed native pickers ─────────────────────────────────────────────────────

/// A dark-themed [showDatePicker] matching the app surfaces.
Future<DateTime?> showThemedDatePicker(
  BuildContext context, {
  DateTime? initial,
}) {
  final now = DateTime.now();
  final seed = initial ?? now;
  return showDatePicker(
    context: context,
    initialDate: seed,
    firstDate: now.subtract(const Duration(days: 365)),
    lastDate: now.add(const Duration(days: 365 * 3)),
    builder: (ctx, child) => Theme(
      data: Theme.of(ctx).copyWith(
        colorScheme: ColorScheme.dark(
          primary: AppColors.accent,
          surface: AppColors.bgSurfaceElevated,
        ),
      ),
      child: child!,
    ),
  );
}

/// A dark-themed [showTimePicker] matching the app surfaces.
Future<TimeOfDay?> showThemedTimePicker(
  BuildContext context, {
  TimeOfDay? initial,
}) {
  return showTimePicker(
    context: context,
    initialTime: initial ?? const TimeOfDay(hour: 9, minute: 0),
    builder: (ctx, child) => Theme(
      data: Theme.of(ctx).copyWith(
        colorScheme: ColorScheme.dark(
          primary: AppColors.accent,
          surface: AppColors.bgSurfaceElevated,
        ),
      ),
      child: child!,
    ),
  );
}
