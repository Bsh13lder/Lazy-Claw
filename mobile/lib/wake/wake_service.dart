/// Controls the always-on wake-word foreground service. The real implementation
/// (`ForegroundWakeService`) drives flutter_foreground_task + Vosk; tests use a
/// fake. [start] returns false if it could not arm (e.g. microphone denied).
library;

abstract interface class WakeService {
  Future<bool> start();
  Future<void> stop();
  Future<bool> isRunning();
}
