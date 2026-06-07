import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../providers/documents_provider.dart';
import '../../repositories/documents_repository.dart';
import 'doc_ai_box.dart';
import 'univer_parse.dart';

/// Read-only viewer for a single sheet, plus the ✨ AI-edit box.
///
/// Renders the FIRST worksheet of the Univer `IWorkbookData` snapshot as a
/// scrollable grid of display values. Manual cell editing is out of scope for
/// this pass — edits go through the AI box (which returns a fresh snapshot we
/// re-render in place).
class SheetViewerScreen extends ConsumerStatefulWidget {
  const SheetViewerScreen({super.key, required this.id, required this.name});

  final String id;
  final String name;

  @override
  ConsumerState<SheetViewerScreen> createState() => _SheetViewerScreenState();
}

class _SheetViewerScreenState extends ConsumerState<SheetViewerScreen> {
  bool _loading = true;
  bool _applying = false;
  String? _error;
  SheetGrid? _grid;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final repo = ref.read(documentsRepositoryProvider);
      final detail = await repo.getPayload(DocKind.sheets, widget.id);
      if (!mounted) return;
      setState(() {
        _grid = parseSheetGrid(detail.payload);
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = 'Could not open this sheet. Pull to retry.';
        _loading = false;
      });
    }
  }

  Future<void> _openAi() async {
    final instruction = await DocAiBox.show(context, kindLabel: 'sheet');
    if (instruction == null || !mounted) return;
    setState(() => _applying = true);
    try {
      final repo = ref.read(documentsRepositoryProvider);
      final result = await repo.aiEdit(DocKind.sheets, widget.id, instruction);
      if (!mounted) return;
      setState(() => _applying = false);
      if (result.ok && result.snapshot != null) {
        setState(() => _grid = parseSheetGrid(result.snapshot));
        // The list's updated_at moved — refresh it lazily.
        ref.read(documentsListProvider(DocKind.sheets).notifier).refresh();
        _snack(result.summary ?? 'Sheet updated.');
      } else {
        _snack(result.error ?? 'AI could not apply that change.', error: true);
      }
    } catch (_) {
      if (!mounted) return;
      setState(() => _applying = false);
      _snack('AI edit failed. Try again.', error: true);
    }
  }

  void _snack(String msg, {bool error = false}) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        backgroundColor: AppColors.bgSurfaceElevated,
        content: Text(
          msg,
          style: AppText.body.copyWith(
            color: error ? AppColors.error : AppColors.textPrimary,
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return LzScaffold(
      appBar: LzAppBar(
        title: widget.name,
        actions: [
          LzIconButton(
            icon: Icons.auto_awesome,
            tooltip: 'Ask AI to edit',
            accent: true,
            onPressed: _applying ? null : _openAi,
          ),
        ],
      ),
      body: Stack(
        children: [
          _buildBody(),
          if (_applying) const AiApplyingOverlay(),
        ],
      ),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return LzSkeleton.list(count: 5);
    }
    if (_error != null) {
      return LzErrorState(message: _error!, onRetry: _load);
    }
    final grid = _grid;
    if (grid == null || grid.isEmpty) {
      return const LzEmptyState(
        icon: Icons.grid_off_rounded,
        title: 'Empty sheet',
        hint: 'This sheet has no data yet. Use ✨ to add some.',
      );
    }
    return _SheetTable(grid: grid);
  }
}

// ── Grid table ───────────────────────────────────────────────────────────────

class _SheetTable extends StatelessWidget {
  const _SheetTable({required this.grid});

  final SheetGrid grid;

  static const double _cellW = 104;
  static const double _gutterW = 44;
  static const double _rowH = 34;

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: SingleChildScrollView(
        padding: const EdgeInsets.only(bottom: AppSpacing.xxl),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _headerRow(),
            for (var r = 0; r < grid.rowCount; r++) _dataRow(r),
          ],
        ),
      ),
    );
  }

  Widget _headerRow() {
    return Row(
      children: [
        _gutterCell(''),
        for (var c = 0; c < grid.colCount; c++)
          _cell(colToLetter(c), header: true, align: TextAlign.center),
      ],
    );
  }

  Widget _dataRow(int r) {
    return Row(
      children: [
        _gutterCell('${r + 1}'),
        for (var c = 0; c < grid.colCount; c++) _cell(grid.rows[r][c]),
      ],
    );
  }

  Widget _gutterCell(String text) => Container(
        width: _gutterW,
        height: _rowH,
        alignment: Alignment.center,
        decoration: const BoxDecoration(
          color: AppColors.bgSurfaceElevated,
          border: Border(
            right: BorderSide(color: AppColors.borderSubtle),
            bottom: BorderSide(color: AppColors.borderSubtle),
          ),
        ),
        child: Text(
          text,
          style: AppText.caption.copyWith(color: AppColors.textMuted),
        ),
      );

  Widget _cell(
    String text, {
    bool header = false,
    TextAlign align = TextAlign.left,
  }) {
    return Container(
      width: _cellW,
      height: _rowH,
      alignment: align == TextAlign.center
          ? Alignment.center
          : Alignment.centerLeft,
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm),
      decoration: BoxDecoration(
        color: header ? AppColors.bgSurfaceElevated : AppColors.bgSurface,
        border: const Border(
          right: BorderSide(color: AppColors.borderSubtle),
          bottom: BorderSide(color: AppColors.borderSubtle),
        ),
      ),
      child: Text(
        text,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        textAlign: align,
        style: header
            ? AppText.caption
                .copyWith(color: AppColors.textSecondary, fontWeight: FontWeight.w700)
            : AppText.body.copyWith(fontSize: 13),
      ),
    );
  }
}
