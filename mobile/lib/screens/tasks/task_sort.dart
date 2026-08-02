import '../../models/subtask.dart';
import '../../models/task.dart';

/// Stable done-last partition: pending tasks first (original relative order),
/// completed after (original relative order). Returns a NEW list — display
/// ordering only, never a storage rewrite.
List<Task> sortDoneLast(List<Task> tasks) => [
      for (final t in tasks) if (!t.isDone) t,
      for (final t in tasks) if (t.isDone) t,
    ];

/// The checklist equivalent: unchecked sub-tasks first, checked sink to the
/// bottom. Display-only — the stored `steps` array order is never rewritten,
/// so unticking restores the item's original position.
List<Subtask> sortSubtasksDoneLast(List<Subtask> subtasks) => [
      for (final s in subtasks) if (!s.done) s,
      for (final s in subtasks) if (s.done) s,
    ];
