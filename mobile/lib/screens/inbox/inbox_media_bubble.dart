// Media bubble for inbox messages that carry a voice note / photo / file.
//
// Bytes are fetched on demand from
// `GET /api/inbox/threads/{id}/media/{messageId}` (rate-limited server-side),
// so scrolling a long thread never hammers WhatsApp media downloads:
//   - audio       → play button; fetch on first tap, then play/pause inline
//   - image       → tap-to-load preview; tap again for a fullscreen viewer
//   - video/file  → metadata chip (kind icon + name + size) — playback later
//
// Kit constraints: tokens only (AppColors/AppText/AppSpacing/AppRadii), no
// hard-coded colors/sizes.

library;

import 'dart:typed_data';

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../comms/inbox_models.dart';
import '../../comms/inbox_providers.dart';
import '../../ui/ui.dart';

enum _LoadState { idle, loading, ready, error }

class InboxMediaBubble extends ConsumerStatefulWidget {
  const InboxMediaBubble({
    super.key,
    required this.threadId,
    required this.messageId,
    required this.media,
    required this.isMine,
  });

  final String threadId;
  final String messageId;
  final InboxMedia media;
  final bool isMine;

  @override
  ConsumerState<InboxMediaBubble> createState() => _InboxMediaBubbleState();
}

class _InboxMediaBubbleState extends ConsumerState<InboxMediaBubble> {
  _LoadState _state = _LoadState.idle;
  Uint8List? _bytes;

  AudioPlayer? _player;
  bool _playing = false;

  @override
  void dispose() {
    _player?.dispose();
    super.dispose();
  }

  Future<Uint8List?> _fetchBytes() async {
    if (_bytes != null) return _bytes;
    setState(() => _state = _LoadState.loading);
    try {
      final raw = await ref
          .read(inboxRepositoryProvider)
          .fetchMedia(widget.threadId, widget.messageId);
      final bytes = Uint8List.fromList(raw);
      if (!mounted) return null;
      setState(() {
        _bytes = bytes;
        _state = _LoadState.ready;
      });
      return bytes;
    } catch (_) {
      if (!mounted) return null;
      setState(() => _state = _LoadState.error);
      return null;
    }
  }

  // ── Audio ───────────────────────────────────────────────────────────────────

  Future<void> _toggleAudio() async {
    if (_playing) {
      await _player?.pause();
      if (mounted) setState(() => _playing = false);
      return;
    }
    final bytes = await _fetchBytes();
    if (bytes == null || bytes.isEmpty) return;

    _player ??= AudioPlayer()
      ..onPlayerComplete.listen((_) {
        if (mounted) setState(() => _playing = false);
      });
    await _player!.play(BytesSource(bytes));
    if (mounted) setState(() => _playing = true);
  }

  // ── Image ───────────────────────────────────────────────────────────────────

  Future<void> _onImageTap() async {
    if (_bytes == null) {
      await _fetchBytes();
      return;
    }
    if (!mounted) return;
    // Fullscreen pinch-zoom viewer.
    showDialog<void>(
      context: context,
      barrierColor: AppColors.bgBase.withValues(alpha: 0.92),
      builder: (_) => GestureDetector(
        onTap: () => Navigator.of(context).pop(),
        child: InteractiveViewer(
          child: Center(child: Image.memory(_bytes!)),
        ),
      ),
    );
  }

  // ── Build ───────────────────────────────────────────────────────────────────

  Color get _fg => widget.isMine ? AppColors.onAccent : AppColors.textPrimary;
  Color get _fgMuted =>
      widget.isMine ? AppColors.onAccent.withValues(alpha: 0.6) : AppColors.textMuted;

  @override
  Widget build(BuildContext context) {
    switch (widget.media.kind) {
      case 'audio':
        return _buildAudio();
      case 'image':
      case 'sticker':
        return _buildImage();
      default:
        return _buildFileChip();
    }
  }

  Widget _buildAudio() {
    final label = widget.media.voiceNote ? 'Voice note' : 'Audio';
    final secs = widget.media.seconds;
    final duration = secs == null
        ? ''
        : ' · ${(secs ~/ 60)}:${(secs % 60).toString().padLeft(2, '0')}';
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        IconButton(
          onPressed: _state == _LoadState.loading ? null : _toggleAudio,
          icon: _state == _LoadState.loading
              ? SizedBox(
                  width: AppSpacing.lg,
                  height: AppSpacing.lg,
                  child: CircularProgressIndicator(strokeWidth: 2, color: _fg),
                )
              : Icon(
                  _playing
                      ? Icons.pause_circle_filled_rounded
                      : Icons.play_circle_fill_rounded,
                  color: _fg,
                  size: 36,
                ),
        ),
        Flexible(
          child: Text(
            _state == _LoadState.error
                ? 'Could not load audio'
                : '$label$duration',
            style: AppText.body.copyWith(color: _fg),
          ),
        ),
      ],
    );
  }

  Widget _buildImage() {
    if (_bytes != null) {
      return GestureDetector(
        onTap: _onImageTap,
        child: ClipRRect(
          borderRadius: AppRadii.rMd,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 280),
            child: Image.memory(_bytes!, fit: BoxFit.cover),
          ),
        ),
      );
    }
    final hint = switch (_state) {
      _LoadState.loading => 'Loading…',
      _LoadState.error => 'Could not load photo — tap to retry',
      _ => 'Tap to view',
    };
    return GestureDetector(
      onTap: _state == _LoadState.loading ? null : _onImageTap,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.photo_rounded, color: _fg),
          AppSpacing.hGap(AppSpacing.sm),
          Flexible(
            child: Text(
              '${widget.media.kind == 'sticker' ? 'Sticker' : 'Photo'} · $hint',
              style: AppText.body.copyWith(color: _fgMuted),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildFileChip() {
    final media = widget.media;
    final icon = media.kind == 'video'
        ? Icons.videocam_rounded
        : Icons.insert_drive_file_rounded;
    final size = media.sizeBytes;
    final sizeLabel =
        size == null ? '' : ' · ${(size / 1024).toStringAsFixed(0)} KB';
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, color: _fg),
        AppSpacing.hGap(AppSpacing.sm),
        Flexible(
          child: Text(
            '${media.fileName ?? media.kind}$sizeLabel',
            style: AppText.body.copyWith(color: _fg),
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }
}
