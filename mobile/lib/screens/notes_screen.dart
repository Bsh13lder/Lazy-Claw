import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/note.dart';
import '../providers/notes_provider.dart';
// reachableProvider lives in tasks_provider (shared offline-first infra).
import '../providers/tasks_provider.dart' show reachableProvider;

// ── Notes list screen ──────────────────────────────────────────────────────

class NotesScreen extends ConsumerStatefulWidget {
  const NotesScreen({super.key});

  @override
  ConsumerState<NotesScreen> createState() => _NotesScreenState();
}

class _NotesScreenState extends ConsumerState<NotesScreen> {
  final _searchController = TextEditingController();
  bool _searching = false;

  @override
  void initState() {
    super.initState();
    Future.microtask(() => ref.read(notesProvider.notifier).load());
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    final query = _searchController.text.trim();
    if (query.isNotEmpty) {
      await ref.read(notesProvider.notifier).search(query);
    } else {
      await ref.read(notesProvider.notifier).load();
    }
  }

  void _onSearchChanged(String value) {
    // Debounce: run search on submit / clear on empty.
    if (value.isEmpty) {
      ref.read(notesProvider.notifier).load();
    }
  }

  void _submitSearch(String value) {
    final q = value.trim();
    if (q.isEmpty) {
      ref.read(notesProvider.notifier).load();
    } else {
      ref.read(notesProvider.notifier).search(q);
    }
  }

  Future<void> _openCreateDialog() async {
    final result = await showDialog<({String title, String content})>(
      context: context,
      builder: (ctx) => const _NoteCreateDialog(),
    );
    if (result == null) return;
    if (!mounted) return;
    await ref.read(notesProvider.notifier).createNote(
          title: result.title.isEmpty ? null : result.title,
          content: result.content,
        );
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(notesProvider);

    ref.listen<NotesState>(notesProvider, (prev, next) {
      if (next.error != null && next.error != prev?.error) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(next.error!),
            action: SnackBarAction(
              label: 'Dismiss',
              onPressed: () => ref.read(notesProvider.notifier).clearError(),
            ),
          ),
        );
      }
    });

    return Scaffold(
      appBar: AppBar(
        title: _searching
            ? TextField(
                controller: _searchController,
                autofocus: true,
                style: const TextStyle(color: Colors.white),
                cursorColor: Colors.white,
                decoration: const InputDecoration(
                  hintText: 'Search notes…',
                  hintStyle: TextStyle(color: Colors.white54),
                  border: InputBorder.none,
                ),
                onChanged: _onSearchChanged,
                onSubmitted: _submitSearch,
                textInputAction: TextInputAction.search,
              )
            : const Text('Notes'),
        actions: [
          IconButton(
            icon: Icon(_searching ? Icons.close : Icons.search),
            tooltip: _searching ? 'Cancel search' : 'Search',
            onPressed: () {
              setState(() => _searching = !_searching);
              if (!_searching) {
                _searchController.clear();
                ref.read(notesProvider.notifier).load();
              }
            },
          ),
        ],
      ),
      body: Column(
        children: [
          if (!ref.watch(reachableProvider)) const _OfflineBanner(),
          Expanded(child: _buildBody(context, state)),
        ],
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: _openCreateDialog,
        tooltip: 'New note',
        child: const Icon(Icons.add),
      ),
    );
  }

  Widget _buildBody(BuildContext context, NotesState state) {
    if (state.isLoading && state.notes.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }

    if (!state.isLoading && state.notes.isEmpty && state.error == null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.notes_outlined,
                size: 56, color: Colors.grey.shade600),
            const SizedBox(height: 12),
            Text(
              state.searchQuery.isNotEmpty
                  ? 'No notes match "${state.searchQuery}"'
                  : 'No notes yet — tap + to create one',
              style: TextStyle(color: Colors.grey.shade500),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView.separated(
        padding: const EdgeInsets.only(bottom: 88),
        itemCount: state.sortedNotes.length,
        separatorBuilder: (_, i) => const Divider(height: 1),
        itemBuilder: (context, index) {
          final note = state.sortedNotes[index];
          return _NoteTile(
            note: note,
            pendingSync: state.dirtyIds.contains(note.id),
            onTap: () => _openNote(context, note),
            onDelete: () =>
                ref.read(notesProvider.notifier).deleteNote(note.id),
          );
        },
      ),
    );
  }

  Future<void> _openNote(BuildContext context, Note note) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => NoteDetailScreen(note: note),
      ),
    );
    // Reload in case the note was edited inside the detail screen.
    if (mounted) await _refresh();
  }
}

// ── Note tile ──────────────────────────────────────────────────────────────

class _NoteTile extends StatelessWidget {
  final Note note;
  final bool pendingSync;
  final VoidCallback onTap;
  final VoidCallback onDelete;

  const _NoteTile({
    required this.note,
    required this.pendingSync,
    required this.onTap,
    required this.onDelete,
  });

  @override
  Widget build(BuildContext context) {
    return Dismissible(
      key: ValueKey(note.id),
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
                title: const Text('Delete note?'),
                content: Text(
                  (note.title != null || note.contentPreview.isNotEmpty)
                      ? (note.title ?? note.contentPreview)
                      : 'This note',
                ),
                actions: [
                  TextButton(
                    onPressed: () => Navigator.pop(ctx, false),
                    child: const Text('Cancel'),
                  ),
                  FilledButton(
                    onPressed: () => Navigator.pop(ctx, true),
                    child: const Text('Delete'),
                  ),
                ],
              ),
            ) ??
            false;
      },
      onDismissed: (_) => onDelete(),
      child: ListTile(
        onTap: onTap,
        leading: note.pinned
            ? Icon(Icons.push_pin, size: 18, color: Theme.of(context).colorScheme.primary)
            : const SizedBox.shrink(),
        title: Text(
          note.title ?? (note.contentPreview.isNotEmpty ? note.contentPreview : 'Untitled'),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        subtitle: note.title != null && note.contentPreview.isNotEmpty
            ? Text(
                note.contentPreview,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context)
                    .textTheme
                    .bodySmall
                    ?.copyWith(color: Colors.grey.shade500),
              )
            : null,
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (pendingSync) ...[
              Tooltip(
                message: 'Not synced yet — pending upload',
                child: Icon(Icons.cloud_off_outlined,
                    size: 18, color: Colors.grey.shade500),
              ),
              const SizedBox(width: 6),
            ],
            if (note.tags.isNotEmpty)
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.surfaceContainerHigh,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  note.tags.first,
                  style: Theme.of(context).textTheme.labelSmall,
                ),
              ),
            const SizedBox(width: 4),
            Icon(Icons.chevron_right, color: Colors.grey.shade600, size: 18),
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

// ── Note detail / edit screen ──────────────────────────────────────────────

class NoteDetailScreen extends ConsumerStatefulWidget {
  final Note note;
  const NoteDetailScreen({super.key, required this.note});

  @override
  ConsumerState<NoteDetailScreen> createState() => _NoteDetailScreenState();
}

class _NoteDetailScreenState extends ConsumerState<NoteDetailScreen> {
  bool _editing = false;
  late final TextEditingController _titleController;
  late final TextEditingController _contentController;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _titleController =
        TextEditingController(text: widget.note.title ?? '');
    _contentController =
        TextEditingController(text: widget.note.content);
  }

  @override
  void dispose() {
    _titleController.dispose();
    _contentController.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final title = _titleController.text.trim();
    final content = _contentController.text;
    setState(() => _saving = true);
    await ref.read(notesProvider.notifier).updateNote(
          widget.note.id,
          title: title.isEmpty ? null : title,
          content: content,
        );
    if (mounted) setState(() {_editing = false; _saving = false;});
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(
        title: _editing
            ? TextField(
                controller: _titleController,
                style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
                decoration: const InputDecoration(
                  hintText: 'Note title (optional)',
                  border: InputBorder.none,
                ),
              )
            : Text(
                widget.note.title ?? 'Untitled',
                style: const TextStyle(fontSize: 18),
              ),
        actions: [
          if (_saving)
            const Padding(
              padding: EdgeInsets.all(14),
              child: SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2)),
            )
          else if (_editing)
            TextButton(onPressed: _save, child: const Text('Save'))
          else
            IconButton(
              icon: const Icon(Icons.edit_outlined),
              tooltip: 'Edit',
              onPressed: () => setState(() => _editing = true),
            ),
        ],
      ),
      body: _editing ? _buildEditor(colorScheme) : _buildViewer(),
    );
  }

  Widget _buildViewer() {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (widget.note.tags.isNotEmpty) ...[
            Wrap(
              spacing: 6,
              children: widget.note.tags
                  .map((t) => Chip(
                        label: Text(t, style: const TextStyle(fontSize: 12)),
                        padding: EdgeInsets.zero,
                        visualDensity: VisualDensity.compact,
                      ))
                  .toList(),
            ),
            const SizedBox(height: 12),
          ],
          MarkdownBody(
            data: widget.note.content,
            selectable: true,
          ),
          const SizedBox(height: 24),
          Text(
            'Updated ${_formatDate(widget.note.updatedAt)}',
            style: TextStyle(fontSize: 11, color: Colors.grey.shade500),
          ),
        ],
      ),
    );
  }

  Widget _buildEditor(ColorScheme colorScheme) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
      child: TextField(
        controller: _contentController,
        maxLines: null,
        expands: true,
        textAlignVertical: TextAlignVertical.top,
        style: const TextStyle(fontFamily: 'monospace', fontSize: 14, height: 1.5),
        decoration: const InputDecoration(
          hintText: 'Write markdown here…',
          border: InputBorder.none,
        ),
      ),
    );
  }

  String _formatDate(String iso) {
    if (iso.isEmpty) return '';
    try {
      final dt = DateTime.parse(iso).toLocal();
      return '${dt.year}-${_pad(dt.month)}-${_pad(dt.day)} '
          '${_pad(dt.hour)}:${_pad(dt.minute)}';
    } catch (_) {
      return iso;
    }
  }

  String _pad(int n) => n.toString().padLeft(2, '0');
}

// ── Create note dialog ─────────────────────────────────────────────────────

class _NoteCreateDialog extends StatefulWidget {
  const _NoteCreateDialog();

  @override
  State<_NoteCreateDialog> createState() => _NoteCreateDialogState();
}

class _NoteCreateDialogState extends State<_NoteCreateDialog> {
  final _titleController = TextEditingController();
  final _contentController = TextEditingController();

  @override
  void dispose() {
    _titleController.dispose();
    _contentController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('New note'),
      content: SizedBox(
        width: double.maxFinite,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: _titleController,
              decoration: const InputDecoration(
                labelText: 'Title (optional)',
                border: OutlineInputBorder(),
                isDense: true,
              ),
              textInputAction: TextInputAction.next,
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _contentController,
              decoration: const InputDecoration(
                labelText: 'Content',
                hintText: 'Write markdown…',
                border: OutlineInputBorder(),
                isDense: true,
              ),
              maxLines: 5,
              autofocus: true,
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () {
            final content = _contentController.text.trim();
            if (content.isEmpty) return;
            Navigator.pop(
              context,
              (title: _titleController.text, content: content),
            );
          },
          child: const Text('Create'),
        ),
      ],
    );
  }
}
