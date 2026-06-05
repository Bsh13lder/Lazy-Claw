import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/task.dart';
import '../providers/tasks_provider.dart';

class TasksScreen extends ConsumerStatefulWidget {
  const TasksScreen({super.key});

  @override
  ConsumerState<TasksScreen> createState() => _TasksScreenState();
}

class _TasksScreenState extends ConsumerState<TasksScreen> {
  final _addController = TextEditingController();
  bool _adding = false;

  @override
  void initState() {
    super.initState();
    // Load tasks on first render.
    Future.microtask(() => ref.read(tasksProvider.notifier).load());
  }

  @override
  void dispose() {
    _addController.dispose();
    super.dispose();
  }

  Future<void> _refresh() => ref.read(tasksProvider.notifier).load();

  Future<void> _submitAdd() async {
    final title = _addController.text.trim();
    if (title.isEmpty) return;
    setState(() => _adding = true);
    await ref.read(tasksProvider.notifier).addTask(title);
    _addController.clear();
    if (mounted) setState(() => _adding = false);
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(tasksProvider);

    // Show error snackbar on new errors.
    ref.listen<TasksState>(tasksProvider, (prev, next) {
      if (next.error != null && next.error != prev?.error) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(next.error!),
            action: SnackBarAction(
              label: 'Dismiss',
              onPressed: () =>
                  ref.read(tasksProvider.notifier).clearError(),
            ),
          ),
        );
      }
    });

    return Scaffold(
      appBar: AppBar(title: const Text('Tasks')),
      body: Column(
        children: [
          Expanded(child: _buildBody(context, state)),
          _buildAddRow(context),
        ],
      ),
    );
  }

  Widget _buildBody(BuildContext context, TasksState state) {
    if (state.isLoading && state.tasks.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }

    if (!state.isLoading && state.tasks.isEmpty && state.error == null) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.check_circle_outline, size: 56, color: Colors.grey),
            SizedBox(height: 12),
            Text('No tasks yet — add one below',
                style: TextStyle(color: Colors.grey)),
          ],
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView.separated(
        padding: const EdgeInsets.symmetric(vertical: 8),
        itemCount: state.tasks.length,
        separatorBuilder: (context, index) => const Divider(height: 1),
        itemBuilder: (context, index) {
          final task = state.tasks[index];
          return _TaskTile(
            task: task,
            onToggle: () =>
                ref.read(tasksProvider.notifier).completeTask(task.id),
            onDelete: () =>
                ref.read(tasksProvider.notifier).deleteTask(task.id),
          );
        },
      ),
    );
  }

  Widget _buildAddRow(BuildContext context) {
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 8, 8, 8),
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller: _addController,
                decoration: const InputDecoration(
                  hintText: 'Add a task…',
                  border: OutlineInputBorder(),
                  isDense: true,
                  contentPadding:
                      EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                ),
                textInputAction: TextInputAction.done,
                onSubmitted: (_) => _submitAdd(),
              ),
            ),
            const SizedBox(width: 8),
            _adding
                ? const SizedBox(
                    width: 40,
                    height: 40,
                    child: Padding(
                      padding: EdgeInsets.all(8),
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                  )
                : IconButton(
                    icon: const Icon(Icons.add),
                    tooltip: 'Add task',
                    onPressed: _submitAdd,
                  ),
          ],
        ),
      ),
    );
  }
}

// ── Task tile ──────────────────────────────────────────────────────────────

class _TaskTile extends StatelessWidget {
  final Task task;
  final VoidCallback onToggle;
  final VoidCallback onDelete;

  const _TaskTile({
    required this.task,
    required this.onToggle,
    required this.onDelete,
  });

  @override
  Widget build(BuildContext context) {
    final isDone = task.isDone;
    final priorityColor = _priorityColor(task.priority, context);

    return Dismissible(
      key: ValueKey(task.id),
      direction: DismissDirection.endToStart,
      background: Container(
        alignment: Alignment.centerRight,
        padding: const EdgeInsets.symmetric(horizontal: 20),
        color: Colors.red.shade600,
        child: const Icon(Icons.delete_outline, color: Colors.white),
      ),
      confirmDismiss: (_) async {
        return await showDialog<bool>(
              context: context,
              builder: (ctx) => AlertDialog(
                title: const Text('Delete task?'),
                content: Text(task.title),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(ctx, false),
                      child: const Text('Cancel')),
                  FilledButton(
                      onPressed: () => Navigator.pop(ctx, true),
                      child: const Text('Delete')),
                ],
              ),
            ) ??
            false;
      },
      onDismissed: (_) => onDelete(),
      child: ListTile(
        leading: Checkbox(
          value: isDone,
          onChanged: isDone ? null : (_) => onToggle(),
          activeColor: Theme.of(context).colorScheme.primary,
        ),
        title: Text(
          task.title,
          style: isDone
              ? const TextStyle(
                  decoration: TextDecoration.lineThrough,
                  color: Colors.grey,
                )
              : null,
        ),
        subtitle: _buildSubtitle(context),
        trailing: _buildPriorityChip(priorityColor),
      ),
    );
  }

  Widget? _buildSubtitle(BuildContext context) {
    final parts = <String>[];
    if (task.category != null) parts.add(task.category!);
    if (task.dueDate != null) parts.add('Due ${task.dueDate}');
    if (parts.isEmpty) return null;
    return Text(parts.join(' · '),
        style: Theme.of(context)
            .textTheme
            .bodySmall
            ?.copyWith(color: Colors.grey.shade600));
  }

  Widget _buildPriorityChip(Color color) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: color.withAlpha(30),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: color.withAlpha(100)),
      ),
      child: Text(
        task.priority,
        style: TextStyle(fontSize: 11, color: color, fontWeight: FontWeight.w600),
      ),
    );
  }

  Color _priorityColor(String priority, BuildContext context) {
    switch (priority) {
      case 'urgent':
        return Colors.red.shade700;
      case 'high':
        return Colors.orange.shade700;
      case 'medium':
        return Colors.blue.shade600;
      default:
        return Colors.grey.shade600;
    }
  }
}
