import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/expense.dart';
import '../models/project.dart';
import '../providers/budgets_provider.dart';
import '../providers/tasks_provider.dart' show reachableProvider;

class ExpensesScreen extends ConsumerStatefulWidget {
  const ExpensesScreen({super.key});

  @override
  ConsumerState<ExpensesScreen> createState() => _ExpensesScreenState();
}

class _ExpensesScreenState extends ConsumerState<ExpensesScreen> {
  final _amountController = TextEditingController();
  final _descController = TextEditingController();
  String? _selectedProjectId;

  @override
  void initState() {
    super.initState();
    Future.microtask(() => ref.read(budgetsProvider.notifier).load());
  }

  @override
  void dispose() {
    _amountController.dispose();
    _descController.dispose();
    super.dispose();
  }

  Future<void> _refresh() => ref.read(budgetsProvider.notifier).refresh();

  Future<void> _submitAddExpense() async {
    final amountText = _amountController.text.trim();
    final description = _descController.text.trim();
    final projectId = _selectedProjectId;

    if (amountText.isEmpty || description.isEmpty || projectId == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
            content: Text('Amount, description, and project are required')),
      );
      return;
    }

    final amount = double.tryParse(amountText);
    if (amount == null || amount <= 0) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Please enter a valid amount')),
      );
      return;
    }

    final ok = await ref.read(budgetsProvider.notifier).addExpense(
          projectId,
          amount,
          description,
        );
    if (ok && mounted) {
      _amountController.clear();
      _descController.clear();
      // Keep project selection for quick follow-up entry.
    }
  }

  void _showAddProjectDialog() {
    final nameController = TextEditingController();
    final budgetController = TextEditingController();
    showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('New Project'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: nameController,
              decoration: const InputDecoration(
                labelText: 'Project name',
                border: OutlineInputBorder(),
              ),
              textInputAction: TextInputAction.next,
            ),
            const SizedBox(height: 12),
            TextField(
              controller: budgetController,
              decoration: const InputDecoration(
                labelText: 'Budget (optional)',
                border: OutlineInputBorder(),
                prefixText: '\$ ',
              ),
              keyboardType:
                  const TextInputType.numberWithOptions(decimal: true),
              textInputAction: TextInputAction.done,
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () async {
              final name = nameController.text.trim();
              if (name.isEmpty) return;
              final budget = double.tryParse(budgetController.text.trim());
              Navigator.pop(ctx);
              await ref
                  .read(budgetsProvider.notifier)
                  .addProject(name, budget: budget);
            },
            child: const Text('Create'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(budgetsProvider);

    // Show error snackbar on new errors.
    ref.listen<BudgetsState>(budgetsProvider, (prev, next) {
      if (next.error != null && next.error != prev?.error) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(next.error!),
            action: SnackBarAction(
              label: 'Dismiss',
              onPressed: () => ref.read(budgetsProvider.notifier).clearError(),
            ),
          ),
        );
      }
    });

    // Auto-select first project when list loads and nothing selected yet.
    if (_selectedProjectId == null && state.projects.isNotEmpty) {
      _selectedProjectId = state.projects.first.id;
    }

    final reachable = ref.watch(reachableProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Expenses'),
        actions: [
          IconButton(
            icon: const Icon(Icons.add_business_outlined),
            tooltip: 'New project',
            onPressed: _showAddProjectDialog,
          ),
        ],
      ),
      body: Column(
        children: [
          if (!reachable) const _OfflineBanner(),
          Expanded(child: _buildBody(context, state)),
          _buildAddRow(context, state),
        ],
      ),
    );
  }

  Widget _buildBody(BuildContext context, BudgetsState state) {
    if (state.isLoading && state.projects.isEmpty && state.expenses.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }

    if (!state.isLoading && state.projects.isEmpty && state.error == null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.receipt_long_outlined,
                size: 56, color: Colors.grey),
            const SizedBox(height: 12),
            const Text('No projects yet', style: TextStyle(color: Colors.grey)),
            const SizedBox(height: 8),
            FilledButton.icon(
              onPressed: _showAddProjectDialog,
              icon: const Icon(Icons.add),
              label: const Text('Create a project'),
            ),
          ],
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView(
        padding: const EdgeInsets.symmetric(vertical: 8),
        children: [
          // ── Budget bars section ────────────────────────────────────
          if (state.projects.isNotEmpty) ...[
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
              child: Text(
                'PROJECTS',
                style: Theme.of(context).textTheme.labelSmall?.copyWith(
                      color: Colors.grey,
                      letterSpacing: 1.0,
                    ),
              ),
            ),
            ...state.projects.map((p) => _ProjectCard(
                  project: p,
                  pendingSync: state.dirtyProjectIds.contains(p.id),
                  onDelete: () =>
                      ref.read(budgetsProvider.notifier).removeProject(p.id),
                )),
            const Divider(height: 24),
          ],
          // ── Expenses list section ──────────────────────────────────
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 4),
            child: Text(
              'RECENT EXPENSES',
              style: Theme.of(context).textTheme.labelSmall?.copyWith(
                    color: Colors.grey,
                    letterSpacing: 1.0,
                  ),
            ),
          ),
          if (state.expenses.isEmpty && !state.isLoading)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 24, horizontal: 16),
              child: Center(
                child: Text('No expenses yet — add one below',
                    style: TextStyle(color: Colors.grey)),
              ),
            )
          else
            ...state.expenses.where((e) => !e.isVoid).map((e) => _ExpenseTile(
                  expense: e,
                  projects: state.projects,
                  pendingSync: state.dirtyExpenseIds.contains(e.id),
                  onDelete: () =>
                      ref.read(budgetsProvider.notifier).removeExpense(e.id),
                )),
        ],
      ),
    );
  }

  Widget _buildAddRow(BuildContext context, BudgetsState state) {
    return SafeArea(
      child: Container(
        padding: const EdgeInsets.fromLTRB(12, 8, 8, 8),
        decoration: BoxDecoration(
          color: Theme.of(context).scaffoldBackgroundColor,
          border: Border(
            top: BorderSide(
              color: Theme.of(context).dividerColor,
              width: 0.5,
            ),
          ),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Project picker row
            Row(
              children: [
                const Icon(Icons.folder_outlined, size: 18, color: Colors.grey),
                const SizedBox(width: 6),
                Expanded(
                  child: state.projects.isEmpty
                      ? const Text('No projects',
                          style: TextStyle(color: Colors.grey, fontSize: 13))
                      : DropdownButtonHideUnderline(
                          child: DropdownButton<String>(
                            value: _selectedProjectId,
                            isExpanded: true,
                            isDense: true,
                            hint: const Text('Select project',
                                style: TextStyle(fontSize: 13)),
                            items: state.projects
                                .map((p) => DropdownMenuItem<String>(
                                      value: p.id,
                                      child: Text(p.name,
                                          style: const TextStyle(fontSize: 13)),
                                    ))
                                .toList(),
                            onChanged: (v) =>
                                setState(() => _selectedProjectId = v),
                          ),
                        ),
                ),
              ],
            ),
            const SizedBox(height: 6),
            // Amount + description + submit row
            Row(
              children: [
                SizedBox(
                  width: 90,
                  child: TextField(
                    controller: _amountController,
                    decoration: const InputDecoration(
                      hintText: '0.00',
                      prefixText: '\$ ',
                      border: OutlineInputBorder(),
                      isDense: true,
                      contentPadding:
                          EdgeInsets.symmetric(horizontal: 8, vertical: 10),
                    ),
                    keyboardType:
                        const TextInputType.numberWithOptions(decimal: true),
                    textInputAction: TextInputAction.next,
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: TextField(
                    controller: _descController,
                    decoration: const InputDecoration(
                      hintText: 'Description…',
                      border: OutlineInputBorder(),
                      isDense: true,
                      contentPadding:
                          EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                    ),
                    textInputAction: TextInputAction.done,
                    onSubmitted: (_) => _submitAddExpense(),
                  ),
                ),
                const SizedBox(width: 8),
                state.isSubmitting
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
                        tooltip: 'Add expense',
                        onPressed: _submitAddExpense,
                      ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

// ── Offline banner ───────────────────────────────────────────────────────────

class _OfflineBanner extends StatelessWidget {
  const _OfflineBanner();

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.orange.shade700,
      child: SafeArea(
        bottom: false,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          child: Row(
            children: const [
              Icon(Icons.cloud_off, color: Colors.white, size: 18),
              SizedBox(width: 8),
              Expanded(
                child: Text(
                  'Computer offline — changes will sync',
                  style: TextStyle(color: Colors.white, fontSize: 13),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ── Cloud-off badge (shared) ─────────────────────────────────────────────────

class _PendingSyncBadge extends StatelessWidget {
  const _PendingSyncBadge();

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: 'Not synced yet — pending upload',
      child: Icon(Icons.cloud_off_outlined,
          size: 16, color: Colors.grey.shade500),
    );
  }
}

// ── Project card with budget bar ───────────────────────────────────────────

class _ProjectCard extends StatelessWidget {
  final Project project;
  final bool pendingSync;
  final VoidCallback onDelete;

  const _ProjectCard({
    required this.project,
    required this.pendingSync,
    required this.onDelete,
  });

  @override
  Widget build(BuildContext context) {
    final fraction = project.spentFraction;
    final barColor = project.budgetBarColor;
    final spent = project.spent ?? 0.0;
    final budget = project.budget;
    final currency = project.currency;

    return Dismissible(
      key: ValueKey('project-${project.id}'),
      direction: DismissDirection.endToStart,
      background: Container(
        alignment: Alignment.centerRight,
        padding: const EdgeInsets.symmetric(horizontal: 20),
        margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
        color: Colors.red.shade600,
        child: const Icon(Icons.delete_outline, color: Colors.white),
      ),
      confirmDismiss: (_) async {
        return await showDialog<bool>(
              context: context,
              builder: (ctx) => AlertDialog(
                title: const Text('Delete project?'),
                content: Text(
                    '${project.name} and its budget bar will be removed.'),
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
      child: Card(
        margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      project.name,
                      style: Theme.of(context).textTheme.titleMedium?.copyWith(
                            fontWeight: FontWeight.w600,
                          ),
                    ),
                  ),
                  if (pendingSync) ...[
                    const _PendingSyncBadge(),
                    const SizedBox(width: 6),
                  ],
                  if (project.isArchived)
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 6, vertical: 2),
                      decoration: BoxDecoration(
                        color: Colors.grey.withAlpha(40),
                        borderRadius: BorderRadius.circular(4),
                      ),
                      child: const Text('archived',
                          style: TextStyle(fontSize: 10, color: Colors.grey)),
                    ),
                ],
              ),
              const SizedBox(height: 6),
              if (budget > 0) ...[
                ClipRRect(
                  borderRadius: BorderRadius.circular(4),
                  child: LinearProgressIndicator(
                    value: fraction,
                    backgroundColor: Colors.grey.withAlpha(40),
                    valueColor: AlwaysStoppedAnimation<Color>(barColor),
                    minHeight: 8,
                  ),
                ),
                const SizedBox(height: 4),
                Row(
                  children: [
                    Text(
                      '$currency ${_fmtAmount(spent)} spent',
                      style: Theme.of(context)
                          .textTheme
                          .bodySmall
                          ?.copyWith(color: barColor),
                    ),
                    const Spacer(),
                    Text(
                      'of $currency ${_fmtAmount(budget)}',
                      style: Theme.of(context)
                          .textTheme
                          .bodySmall
                          ?.copyWith(color: Colors.grey),
                    ),
                  ],
                ),
              ] else ...[
                Text(
                  'No budget set',
                  style: Theme.of(context)
                      .textTheme
                      .bodySmall
                      ?.copyWith(color: Colors.grey),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  String _fmtAmount(double v) {
    if (v == v.truncateToDouble()) return v.toInt().toString();
    return v.toStringAsFixed(2);
  }
}

// ── Expense tile ───────────────────────────────────────────────────────────

class _ExpenseTile extends StatelessWidget {
  final Expense expense;
  final List<Project> projects;
  final bool pendingSync;
  final VoidCallback onDelete;

  const _ExpenseTile({
    required this.expense,
    required this.projects,
    required this.pendingSync,
    required this.onDelete,
  });

  @override
  Widget build(BuildContext context) {
    final projectName = expense.projectName ??
        projects
            .where((p) => p.id == expense.projectId)
            .map((p) => p.name)
            .firstOrNull ??
        '';

    return Dismissible(
      key: ValueKey('expense-${expense.id}'),
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
                title: const Text('Delete expense?'),
                content: Text(expense.displayDescription.isEmpty
                    ? 'This expense will be removed.'
                    : expense.displayDescription),
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
        leading: Container(
          width: 40,
          height: 40,
          decoration: BoxDecoration(
            color: Theme.of(context).colorScheme.primaryContainer,
            borderRadius: BorderRadius.circular(8),
          ),
          alignment: Alignment.center,
          child: Icon(
            Icons.receipt_outlined,
            size: 20,
            color: Theme.of(context).colorScheme.onPrimaryContainer,
          ),
        ),
        title: Row(
          children: [
            Expanded(
              child: Text(
                expense.displayDescription.isEmpty
                    ? '(no description)'
                    : expense.displayDescription,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            if (pendingSync) ...[
              const _PendingSyncBadge(),
              const SizedBox(width: 6),
            ],
            Text(
              '${expense.currency} ${_fmtAmount(expense.amount)}',
              style: TextStyle(
                fontWeight: FontWeight.w700,
                color: Theme.of(context).colorScheme.primary,
              ),
            ),
          ],
        ),
        subtitle: projectName.isNotEmpty
            ? Text(
                projectName,
                style: Theme.of(context)
                    .textTheme
                    .bodySmall
                    ?.copyWith(color: Colors.grey.shade600),
              )
            : null,
      ),
    );
  }

  String _fmtAmount(double v) {
    if (v == v.truncateToDouble()) return v.toInt().toString();
    return v.toStringAsFixed(2);
  }
}
