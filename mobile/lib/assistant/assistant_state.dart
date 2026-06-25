/// Immutable view-state for the "Hey Lazy" assistant turn — phase, the user's
/// transcript, the streaming reply, an optional error, and which tier produced
/// the reply. Kept separate from the controller so the widgets that only render
/// state (badge, screen) don't pull in the platform plugins the controller
/// holds.
library;

enum AssistantPhase {
  idle,
  listening,
  thinking,
  speaking,
  error,
  awaitingCloudConsent, // held before the first cloud hop, pending consent
}

/// Where the reply for the current turn was produced — drives the provenance
/// badge so the user always knows whether a turn stayed on the phone.
enum TurnSource { onDevice, cloud }

class AssistantState {
  const AssistantState({
    this.phase = AssistantPhase.idle,
    this.transcript = '',
    this.response = '',
    this.error,
    this.source,
  });

  final AssistantPhase phase;
  final String transcript; // what the user said
  final String response; // Lazy's reply (streams in while thinking)
  final String? error;
  final TurnSource? source; // on-device vs cloud for this turn

  bool get isBusy =>
      phase == AssistantPhase.listening ||
      phase == AssistantPhase.thinking ||
      phase == AssistantPhase.speaking;

  AssistantState copyWith({
    AssistantPhase? phase,
    String? transcript,
    String? response,
    String? error,
    TurnSource? source,
    bool clearError = false,
  }) =>
      AssistantState(
        phase: phase ?? this.phase,
        transcript: transcript ?? this.transcript,
        response: response ?? this.response,
        error: clearError ? null : (error ?? this.error),
        source: source ?? this.source,
      );
}
