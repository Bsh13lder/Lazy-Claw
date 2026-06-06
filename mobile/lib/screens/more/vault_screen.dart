import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../providers/vault_provider.dart';
import '../../repositories/vault_repository.dart';
import '../../ui/ui.dart';

// ── Key-type classification ────────────────────────────────────────────────

enum _KeyType { api, token, secret, other }

_KeyType _classify(String name) {
  final upper = name.toUpperCase();
  if (upper.contains('API') || upper.contains('KEY')) return _KeyType.api;
  if (upper.contains('TOKEN')) return _KeyType.token;
  if (upper.contains('SECRET') || upper.contains('PASSWORD')) {
    return _KeyType.secret;
  }
  return _KeyType.other;
}

IconData _keyIcon(_KeyType type) {
  switch (type) {
    case _KeyType.api:
      return Icons.vpn_key_outlined;
    case _KeyType.token:
      return Icons.shield_outlined;
    case _KeyType.secret:
      return Icons.lock_outlined;
    case _KeyType.other:
      return Icons.lock_outlined;
  }
}

Color _keyColor(_KeyType type) {
  switch (type) {
    case _KeyType.api:
      return AppColors.info;
    case _KeyType.token:
      return AppColors.warn;
    case _KeyType.secret:
      return AppColors.accent;
    case _KeyType.other:
      return AppColors.textMuted;
  }
}

String _keyLabel(_KeyType type) {
  switch (type) {
    case _KeyType.api:
      return 'API Key';
    case _KeyType.token:
      return 'Token';
    case _KeyType.secret:
      return 'Secret';
    case _KeyType.other:
      return 'Credential';
  }
}

// ── Vault entry card ───────────────────────────────────────────────────────

class _VaultEntryCard extends StatelessWidget {
  const _VaultEntryCard({required this.entry, required this.onDelete});

  final VaultEntry entry;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final type = _classify(entry.name);
    final color = _keyColor(type);

    return LzCard(
      padding: EdgeInsets.zero,
      child: LzListTile(
        title: entry.name,
        titleStyle: AppText.body.copyWith(
          fontFamily: 'monospace',
          color: AppColors.textPrimary,
        ),
        leading: Container(
          width: 36,
          height: 36,
          decoration: BoxDecoration(
            color: color.withValues(alpha: 0.14),
            borderRadius: AppRadii.rSm,
          ),
          child: Icon(_keyIcon(type), size: 18, color: color),
        ),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            LzChip(
              label: _keyLabel(type),
              color: color,
              dense: true,
            ),
            const SizedBox(width: AppSpacing.sm),
            LzIconButton(
              icon: Icons.delete_outline,
              tooltip: 'Delete',
              color: AppColors.textMuted,
              onPressed: onDelete,
            ),
          ],
        ),
      ),
    );
  }
}

// ── Add credential bottom sheet ────────────────────────────────────────────

class _AddSecretSheet extends ConsumerStatefulWidget {
  const _AddSecretSheet();

  @override
  ConsumerState<_AddSecretSheet> createState() => _AddSecretSheetState();
}

class _AddSecretSheetState extends ConsumerState<_AddSecretSheet> {
  final _nameCtrl = TextEditingController();
  final _valueCtrl = TextEditingController();
  bool _obscure = true;
  String? _nameError;

  @override
  void dispose() {
    _nameCtrl.dispose();
    _valueCtrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final name = _nameCtrl.text.trim();
    final value = _valueCtrl.text;

    if (name.isEmpty) {
      setState(() => _nameError = 'Key name is required');
      return;
    }
    if (value.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Value is required')),
      );
      return;
    }

    setState(() => _nameError = null);
    final ok = await ref.read(vaultProvider.notifier).addSecret(name, value);
    if (mounted) {
      if (ok) {
        Navigator.of(context).pop(true);
      } else {
        final err = ref.read(vaultProvider).error;
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(err ?? 'Failed to save credential')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final isSubmitting = ref.watch(
      vaultProvider.select((s) => s.isSubmitting),
    );

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        LzTextField(
          controller: _nameCtrl,
          label: 'Key name',
          hint: 'e.g. OPENAI_API_KEY',
          prefixIcon: Icons.vpn_key_outlined,
          errorText: _nameError,
          autofocus: true,
          textInputAction: TextInputAction.next,
        ),
        const SizedBox(height: AppSpacing.lg),
        LzTextField(
          controller: _valueCtrl,
          label: 'Value',
          hint: 'sk-…',
          prefixIcon: Icons.lock_outlined,
          obscureText: _obscure,
          suffix: IconButton(
            icon: Icon(
              _obscure
                  ? Icons.visibility_outlined
                  : Icons.visibility_off_outlined,
              size: 20,
              color: AppColors.textMuted,
            ),
            onPressed: () => setState(() => _obscure = !_obscure),
          ),
          textInputAction: TextInputAction.done,
          onSubmitted: (_) => _submit(),
        ),
        const SizedBox(height: AppSpacing.sm),
        Text(
          'Value will be encrypted with AES-256-GCM before storage.',
          style: AppText.caption.copyWith(color: AppColors.textMuted),
        ),
        const SizedBox(height: AppSpacing.xl),
        LzButton.primary(
          label: 'Save credential',
          icon: Icons.save_outlined,
          loading: isSubmitting,
          expand: true,
          onPressed: isSubmitting ? null : _submit,
        ),
      ],
    );
  }
}

// ── VaultScreen ────────────────────────────────────────────────────────────

class VaultScreen extends ConsumerStatefulWidget {
  const VaultScreen({super.key});

  @override
  ConsumerState<VaultScreen> createState() => _VaultScreenState();
}

class _VaultScreenState extends ConsumerState<VaultScreen> {
  @override
  void initState() {
    super.initState();
    // Trigger the initial load after the first frame.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(vaultProvider.notifier).load();
    });
  }

  Future<void> _confirmDelete(String name) async {
    final confirmed = await LzConfirm.show(
      context,
      title: 'Delete credential',
      message: 'Remove "$name" from the vault? This cannot be undone.',
      confirmLabel: 'Delete',
      danger: true,
    );
    if (confirmed && mounted) {
      final ok = await ref.read(vaultProvider.notifier).deleteSecret(name);
      if (!ok && mounted) {
        final err = ref.read(vaultProvider).error;
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(err ?? 'Failed to delete credential')),
        );
      }
    }
  }

  Future<void> _openAddSheet() async {
    await LzBottomSheet.show<bool>(
      context,
      title: 'Add credential',
      builder: (_) => const _AddSecretSheet(),
    );
  }

  Widget _buildBody(VaultState state) {
    if (state.isLoading) {
      return LzSkeleton.list(
        count: 5,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.md,
        ),
      );
    }

    if (state.error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.xl),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(
                Icons.cloud_off_outlined,
                size: 40,
                color: AppColors.textMuted,
              ),
              const SizedBox(height: AppSpacing.md),
              Text(
                state.error!,
                textAlign: TextAlign.center,
                style: AppText.body.copyWith(color: AppColors.textSecondary),
              ),
              const SizedBox(height: AppSpacing.xl),
              LzButton.secondary(
                label: 'Retry',
                icon: Icons.refresh,
                onPressed: () => ref.read(vaultProvider.notifier).refresh(),
              ),
            ],
          ),
        ),
      );
    }

    if (state.entries.isEmpty) {
      return LzEmptyState(
        icon: Icons.lock_outlined,
        title: 'Vault is empty',
        hint: 'Store API keys and secrets —\nencrypted with AES-256-GCM.',
        actionLabel: 'Add first credential',
        actionIcon: Icons.add,
        onAction: _openAddSheet,
      );
    }

    return LzRefresh(
      onRefresh: () => ref.read(vaultProvider.notifier).refresh(),
      child: ListView.separated(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.lg,
          AppSpacing.md,
          AppSpacing.lg,
          // Extra bottom padding for the FAB.
          AppSpacing.lg + 72,
        ),
        itemCount: state.entries.length,
        separatorBuilder: (context, index) => const SizedBox(height: AppSpacing.sm),
        itemBuilder: (context, index) {
          final entry = state.entries[index];
          return Dismissible(
            key: ValueKey(entry.name),
            direction: DismissDirection.endToStart,
            background: Container(
              alignment: Alignment.centerRight,
              padding: const EdgeInsets.only(right: AppSpacing.xl),
              decoration: BoxDecoration(
                color: AppColors.error.withValues(alpha: 0.12),
                borderRadius: AppRadii.rLg,
              ),
              child: const Icon(
                Icons.delete_outline,
                color: AppColors.error,
              ),
            ),
            confirmDismiss: (_) => LzConfirm.show(
              context,
              title: 'Delete credential',
              message:
                  'Remove "${entry.name}" from the vault? This cannot be undone.',
              confirmLabel: 'Delete',
              danger: true,
            ),
            onDismissed: (_) =>
                ref.read(vaultProvider.notifier).deleteSecret(entry.name),
            child: _VaultEntryCard(
              entry: entry,
              onDelete: () => _confirmDelete(entry.name),
            ),
          );
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(vaultProvider);
    final count = state.entries.length;

    return LzScaffold(
      appBar: LzAppBar(
        title: 'Vault',
        actions: [
          LzIconButton(
            icon: Icons.add,
            tooltip: 'Add credential',
            onPressed: _openAddSheet,
          ),
        ],
      ),
      banner: LzBanner(
        message: 'AES-256-GCM · $count encrypted credential'
            '${count == 1 ? '' : 's'}',
        icon: Icons.shield_outlined,
        variant: LzBannerVariant.info,
        safeAreaTop: false,
      ),
      floatingActionButton: (!state.isLoading && state.entries.isNotEmpty)
          ? FloatingActionButton(
              onPressed: _openAddSheet,
              backgroundColor: AppColors.accent,
              foregroundColor: AppColors.onAccent,
              tooltip: 'Add credential',
              child: const Icon(Icons.add),
            )
          : null,
      body: _buildBody(state),
    );
  }
}
