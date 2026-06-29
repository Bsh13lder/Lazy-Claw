/// A confirmed "Hey Lazy" detection. Carries only the time it fired — the
/// recognizer's audio never leaves the phone and is not retained.
library;

class WakeEvent {
  final DateTime at;
  const WakeEvent(this.at);
}
