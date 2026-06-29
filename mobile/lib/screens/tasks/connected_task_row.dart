import 'package:flutter/material.dart';

import '../../models/project.dart';
import '../../models/subtask.dart';
import '../../models/task.dart';
import 'task_row.dart';

/// Binds a [TaskRow]'s value-only callbacks to id-carrying handlers so the same
/// wiring can be reused by every Tasks surface (the sectioned list AND the
/// Projects view). The row itself stays presentational; the screen owns the
/// `tasksProvider.updateTask` / `setSubtasks` commits.
///
/// Passing a null handler leaves that affordance read-only (the chip renders as
/// a static label), exactly like [TaskRow]'s own contract.
class ConnectedTaskRow extends StatelessWidget {
  const ConnectedTaskRow({
    super.key,
    required this.task,
    required this.pendingSync,
    required this.projects,
    required this.onComplete,
    required this.onDelete,
    required this.onOpen,
    this.onRenameTitle,
    this.onPriorityChanged,
    this.onDueDateChanged,
    this.onCategoryChanged,
    this.onSubtasksChanged,
    this.onReschedule,
  });

  final Task task;
  final bool pendingSync;
  final List<Project> projects;

  final void Function(String id) onComplete;
  final void Function(String id) onDelete;
  final void Function(Task task) onOpen;
  final void Function(String id, String title)? onRenameTitle;
  final void Function(String id, String priority)? onPriorityChanged;
  final void Function(String id, String dueDate)? onDueDateChanged;
  final void Function(String id, String category)? onCategoryChanged;
  final void Function(String id, List<Subtask> subtasks)? onSubtasksChanged;

  /// Opens the Smart Fast Reschedule sheet for [task]. When set, an overdue
  /// card's due chip routes here instead of the plain date picker.
  final void Function(Task task)? onReschedule;

  @override
  Widget build(BuildContext context) {
    final t = task;
    return TaskRow(
      task: t,
      pendingSync: pendingSync,
      projects: projects,
      onComplete: () => onComplete(t.id),
      onDelete: () => onDelete(t.id),
      onTap: () => onOpen(t),
      onReschedule: onReschedule == null ? null : () => onReschedule!(t),
      onTitleChanged:
          onRenameTitle == null ? null : (title) => onRenameTitle!(t.id, title),
      onPriorityChanged: onPriorityChanged == null
          ? null
          : (p) => onPriorityChanged!(t.id, p),
      onDueDateChanged:
          onDueDateChanged == null ? null : (d) => onDueDateChanged!(t.id, d),
      onCategoryChanged:
          onCategoryChanged == null ? null : (c) => onCategoryChanged!(t.id, c),
      onSubtasksChanged: onSubtasksChanged == null
          ? null
          : (s) => onSubtasksChanged!(t.id, s),
    );
  }
}
