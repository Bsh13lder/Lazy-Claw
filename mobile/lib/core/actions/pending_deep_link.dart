import 'package:flutter_riverpod/flutter_riverpod.dart';

/// A pending deep-link route path (e.g. `/inbox/<id>`, `/tasks`,
/// `/notifications`) set when the user taps a server notification — or when the
/// app is cold-started by one. The app shell listens to this and navigates,
/// then clears it.
///
/// Orthogonal to `pendingActionProvider` (which drives the quick-capture
/// sheets via the fixed [AppAction] enum): deep links can target arbitrary
/// routes, including `/inbox/<threadId>`, so they need a free-form path rather
/// than an enum. Replaces the old behaviour where every notification tap forced
/// the Chat tab regardless of what fired.
final pendingDeepLinkProvider = StateProvider<String?>((ref) => null);

/// Shell-branch roots are switched with `go` (they swap the active tab);
/// everything else (`/inbox/<id>`, `/notifications`, `/more/*`, `/assistant`)
/// is a full route pushed OVER the shell.
const Set<String> kShellRootPaths = {
  '/home',
  '/chat',
  '/tasks',
  '/expenses',
  '/documents',
  '/settings',
};
