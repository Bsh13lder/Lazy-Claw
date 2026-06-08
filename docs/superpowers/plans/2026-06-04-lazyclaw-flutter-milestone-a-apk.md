# LazyClaw Flutter — Milestone A: First Installable APK — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a real, installable Android APK of a LazyClaw mobile client that can log in to the user's self-hosted gateway and hold a live streaming agent chat — downloadable from a Download button + QR code in the web Settings page.

**Architecture:** New Flutter project at `lazyclaw/mobile/` harvesting the proven generic scaffolding from the `taskbot_flutter` donor (Dio API client, cookie-session auth state machine, go_router auth guard, dark theme). Auth is cookie-session (`session_id`); chat is a thin WebSocket client over `/ws/chat`. The built `app-release.apk` is served by two new FastAPI routes (`/api/mobile/version`, `/api/mobile/apk`) and surfaced in a new "Mobile App" tab in the React Settings page with a QR for sideloading onto the phone.

**Tech Stack:** Flutter 3.41.6 / Dart 3, Riverpod, go_router, Dio (+ cookie_jar), web_socket_channel, flutter_markdown, flutter_secure_storage; FastAPI (gateway); React 19 (web).

**Scope:** Login + register + live chat + APK delivery only. NOT in this plan: encrypted local DB, offline sync engine, tasks/budgets/notes CRUD, notifications, full chat (tool-call cards / plan gates / inline typed cards), iOS. Those are separate plans.

**Out-of-band prerequisite (user action):** none required up front — Java 17 (Homebrew `openjdk@17`) and the Android SDK are already on this Mac and the donor has built APKs here before. To *install* the finished APK, the user enables "Install unknown apps" on the Mi 15 (covered in Task 18).

---

## File Structure

**Flutter app — `lazyclaw/mobile/`**
- `lib/core/api/api_client.dart` — harvested Dio transport (repointed). One responsibility: HTTP transport + cookie persistence + 401 hook.
- `lib/core/api/api_exceptions.dart` — harvested `ApiError`.
- `lib/core/constants/app_constants.dart` — base URL + app metadata.
- `lib/core/theme/app_theme.dart` — harvested dark theme.
- `lib/core/config/server_config.dart` — persisted gateway base URL (user-editable; self-hosted IP/host).
- `lib/models/user.dart` — auth user model.
- `lib/repositories/auth_repository.dart` — login/register/me/logout REST.
- `lib/providers/auth_provider.dart` — Riverpod auth state machine + `on401→logout`.
- `lib/core/router/app_router.dart` — go_router + auth-guard redirect + ShellRoute.
- `lib/chat/ws_frames.dart` — chat WS frame model + parser (pure, unit-tested).
- `lib/chat/chat_socket.dart` — `web_socket_channel` client (connect w/ cookie, send, stream, reconnect, ping).
- `lib/chat/chat_controller.dart` — Riverpod controller turning frames into a message list + streaming buffer.
- `lib/chat/chat_message.dart` — UI message model.
- `lib/screens/login_screen.dart`, `lib/screens/register_screen.dart`, `lib/screens/chat_screen.dart`, `lib/screens/server_setup_screen.dart`.
- `lib/main.dart` — app entry.
- `test/...` — mirrors `lib/` for the unit/widget tests below.

**Backend — `lazyclaw/lazyclaw/gateway/routes/mobile.py`** (new) — serves APK + version; registered in the gateway app.

**Web — `lazyclaw/web/src/pages/settings/MobileAppTab.tsx`** (new) + a tab entry in `web/src/pages/Settings.tsx`; one API helper in `web/src/api.ts`.

**Build — `lazyclaw/scripts/build-mobile-apk.sh`** (new) — builds the APK, stamps version JSON, copies into the served `dist/` dir.

---

## Task 0: Wire the Android build environment (one-time, this machine)

**Files:** none (environment + Flutter config).

- [ ] **Step 1: Point Flutter at the existing JDK 17**

Run:
```bash
flutter config --jdk-dir "$(brew --prefix openjdk@17)/libexec/openjdk.jdk/Contents/Home"
```
Expected: `Setting "jdk-dir" value to "...openjdk@17..."`.

- [ ] **Step 2: Export ANDROID_HOME for this and future shells**

Run:
```bash
echo 'export ANDROID_HOME="$HOME/Library/Android/sdk"' >> ~/.zshrc
echo 'export PATH="$ANDROID_HOME/platform-tools:$PATH"' >> ~/.zshrc
export ANDROID_HOME="$HOME/Library/Android/sdk"; export PATH="$ANDROID_HOME/platform-tools:$PATH"
adb --version
```
Expected: `adb` prints a version (platform-tools already present).

- [ ] **Step 3: Verify the toolchain via the donor (proves the chain compiles APKs here)**

Run:
```bash
cd ~/Desktop/Code_Projects/taskbot/taskbot_flutter && flutter build apk --debug 2>&1 | tail -5
```
Expected: `✓ Built build/app/outputs/flutter-apk/app-debug.apk`. If it fails on licenses, run `yes | flutter doctor --android-licenses` and retry. **Do not proceed until the donor builds a debug APK.**

- [ ] **Step 4: Commit** (config only — nothing to commit in git; record completion by checking the box).

---

## Task 1: Scaffold the Flutter project and confirm it builds

**Files:**
- Create: `lazyclaw/mobile/` (via `flutter create`)

- [ ] **Step 1: Create the project**

Run:
```bash
cd ~/Desktop/Code_Projects/lazyclaw && flutter create --org com.lazyclaw --project-name lazyclaw_mobile --platforms android,ios mobile
```
Expected: `All done!` and a `mobile/` directory.

- [ ] **Step 2: Confirm OUR new project builds a debug APK**

Run:
```bash
cd ~/Desktop/Code_Projects/lazyclaw/mobile && flutter build apk --debug 2>&1 | tail -3
```
Expected: `✓ Built build/app/outputs/flutter-apk/app-debug.apk`.

- [ ] **Step 3: Add a .gitignore for build artifacts and the served APK**

Append to `lazyclaw/mobile/.gitignore`:
```
/dist/
*.apk
*.keystore
key.properties
```

- [ ] **Step 4: Commit**

```bash
cd ~/Desktop/Code_Projects/lazyclaw
git add mobile/.gitignore mobile/pubspec.yaml mobile/lib mobile/android mobile/ios mobile/test mobile/analysis_options.yaml mobile/README.md
git commit -m "feat(mobile): scaffold lazyclaw flutter app"
```

---

## Task 2: Declare dependencies

**Files:**
- Modify: `lazyclaw/mobile/pubspec.yaml`

- [ ] **Step 1: Add the Milestone-A dependencies**

In `lazyclaw/mobile/pubspec.yaml` under `dependencies:` (keep the existing `flutter:` and `cupertino_icons`):
```yaml
  flutter_riverpod: ^2.6.1
  go_router: ^14.8.1
  dio: ^5.7.0
  dio_cookie_manager: ^3.1.1
  cookie_jar: ^4.0.8
  path_provider: ^2.1.4
  flutter_secure_storage: ^9.2.2
  web_socket_channel: ^3.0.1
  flutter_markdown: ^0.7.4
  lucide_icons: ^0.257.0
```

- [ ] **Step 2: Install and verify resolution**

Run:
```bash
cd ~/Desktop/Code_Projects/lazyclaw/mobile && flutter pub get 2>&1 | tail -3
```
Expected: `Got dependencies!` (or `exit code 0`).

- [ ] **Step 3: Commit**

```bash
git add mobile/pubspec.yaml mobile/pubspec.lock
git commit -m "feat(mobile): add core dependencies"
```

---

## Task 3: Harvest the Dio API client (copy-as-is, repointed)

**Files:**
- Create: `lazyclaw/mobile/lib/core/api/api_client.dart` (from donor)
- Create: `lazyclaw/mobile/lib/core/api/api_exceptions.dart` (from donor)
- Test: `lazyclaw/mobile/test/core/api/api_client_test.dart`

- [ ] **Step 1: Copy the donor files**

Run:
```bash
cd ~/Desktop/Code_Projects/lazyclaw/mobile && mkdir -p lib/core/api
cp ~/Desktop/Code_Projects/taskbot/taskbot_flutter/lib/core/api/api_client.dart lib/core/api/api_client.dart
cp ~/Desktop/Code_Projects/taskbot/taskbot_flutter/lib/core/api/api_exceptions.dart lib/core/api/api_exceptions.dart
```

- [ ] **Step 2: Repoint the session-cookie name**

In `lib/core/api/api_client.dart`, find the `getSessionCookie()` method which reads the cookie named `'session'`. Change the cookie name to `'session_id'` (LazyClaw's cookie name). The line looks like `cookie.name == 'session'` → `cookie.name == 'session_id'`. If `clearSession()` references the donor name too, leave it (it deletes all).

- [ ] **Step 3: Fix imports/package name**

Replace any `package:taskbot_flutter/...` import prefixes in both files with `package:lazyclaw_mobile/...`. Run a quick check:
```bash
grep -rn "taskbot_flutter" lib/core/api/ || echo "clean"
```
Expected: `clean`.

- [ ] **Step 4: Write the failing test for ApiError mapping**

Create `test/core/api/api_client_test.dart`:
```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';

void main() {
  test('ApiError classifies status codes', () {
    expect(ApiError(401, 'x').isUnauthorized, isTrue);
    expect(ApiError(404, 'x').isNotFound, isTrue);
    expect(ApiError(403, 'x').isForbidden, isTrue);
    expect(ApiError(500, 'x').isUnauthorized, isFalse);
  });
}
```

- [ ] **Step 5: Run it to verify it fails (import resolves but asserts run)**

Run:
```bash
flutter test test/core/api/api_client_test.dart 2>&1 | tail -5
```
Expected: PASS if `ApiError` getters exist as harvested; if the getters are named differently in the donor, adjust the test to the real getter names (`isUnauthorized/isNotFound/isForbidden`) — do not invent names.

- [ ] **Step 6: Commit**

```bash
git add mobile/lib/core/api mobile/test/core/api
git commit -m "feat(mobile): harvest dio api client, repoint cookie to session_id"
```

---

## Task 4: Server config + constants

**Files:**
- Create: `lazyclaw/mobile/lib/core/constants/app_constants.dart`
- Create: `lazyclaw/mobile/lib/core/config/server_config.dart`
- Test: `lazyclaw/mobile/test/core/config/server_config_test.dart`

- [ ] **Step 1: Constants**

Create `lib/core/constants/app_constants.dart`:
```dart
/// Default gateway when the user hasn't set one. The self-hosted box on the LAN.
const String kDefaultBaseUrl = 'http://127.0.0.1:18789';
const String kAppVersion = '0.1.0';
const int kAppBuild = 1;
const String kSecureBaseUrlKey = 'lazyclaw_base_url';
```

- [ ] **Step 2: Write the failing test for ServerConfig URL normalization**

Create `test/core/config/server_config_test.dart`:
```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/config/server_config.dart';

void main() {
  test('normalizeBaseUrl strips trailing slash and adds scheme', () {
    expect(ServerConfig.normalizeBaseUrl('192.168.1.5:18789'),
        'http://192.168.1.5:18789');
    expect(ServerConfig.normalizeBaseUrl('https://box.local/'),
        'https://box.local');
    expect(ServerConfig.normalizeBaseUrl('http://x:18789'), 'http://x:18789');
  });

  test('wsUrlFor converts http(s) base to ws(s) /ws/chat', () {
    expect(ServerConfig.wsUrlFor('http://x:18789'), 'ws://x:18789/ws/chat');
    expect(ServerConfig.wsUrlFor('https://box.local'), 'wss://box.local/ws/chat');
  });
}
```

- [ ] **Step 3: Run to verify it fails**

Run: `flutter test test/core/config/server_config_test.dart`
Expected: FAIL — `server_config.dart` does not exist.

- [ ] **Step 4: Implement ServerConfig**

Create `lib/core/config/server_config.dart`:
```dart
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import '../constants/app_constants.dart';

class ServerConfig {
  static const _storage = FlutterSecureStorage();

  static String normalizeBaseUrl(String raw) {
    var v = raw.trim();
    if (!v.startsWith('http://') && !v.startsWith('https://')) {
      v = 'http://$v';
    }
    if (v.endsWith('/')) v = v.substring(0, v.length - 1);
    return v;
  }

  static String wsUrlFor(String baseUrl) {
    final b = normalizeBaseUrl(baseUrl);
    final ws = b.startsWith('https://')
        ? 'wss://${b.substring('https://'.length)}'
        : 'ws://${b.substring('http://'.length)}';
    return '$ws/ws/chat';
  }

  static Future<String> load() async =>
      (await _storage.read(key: kSecureBaseUrlKey)) ?? kDefaultBaseUrl;

  static Future<void> save(String baseUrl) async =>
      _storage.write(key: kSecureBaseUrlKey, value: normalizeBaseUrl(baseUrl));
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `flutter test test/core/config/server_config_test.dart`
Expected: PASS (the two pure-function tests; `load/save` aren't exercised here).

- [ ] **Step 6: Commit**

```bash
git add mobile/lib/core/constants mobile/lib/core/config mobile/test/core/config
git commit -m "feat(mobile): server config + url normalization"
```

---

## Task 5: User model

**Files:**
- Create: `lazyclaw/mobile/lib/models/user.dart`
- Test: `lazyclaw/mobile/test/models/user_test.dart`

- [ ] **Step 1: Write the failing test**

Create `test/models/user_test.dart`:
```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/user.dart';

void main() {
  test('User.fromJson reads LazyClaw auth shape', () {
    final u = User.fromJson({
      'id': 'uuid-1', 'username': 'sam',
      'display_name': 'Sam', 'role': 'admin',
    });
    expect(u.id, 'uuid-1');
    expect(u.username, 'sam');
    expect(u.displayName, 'Sam');
    expect(u.role, 'admin');
  });

  test('User.fromJson tolerates null display_name', () {
    final u = User.fromJson({'id': 'x', 'username': 'y', 'role': 'user'});
    expect(u.displayName, isNull);
  });
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `flutter test test/models/user_test.dart` — Expected: FAIL (no `user.dart`).

- [ ] **Step 3: Implement**

Create `lib/models/user.dart`:
```dart
class User {
  final String id;
  final String username;
  final String? displayName;
  final String role;

  const User({
    required this.id,
    required this.username,
    required this.role,
    this.displayName,
  });

  factory User.fromJson(Map<String, dynamic> json) => User(
        id: json['id'] as String,
        username: json['username'] as String,
        displayName: json['display_name'] as String?,
        role: (json['role'] as String?) ?? 'user',
      );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `flutter test test/models/user_test.dart` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/models mobile/test/models
git commit -m "feat(mobile): user model"
```

---

## Task 6: Auth repository (REST against LazyClaw)

**Files:**
- Create: `lazyclaw/mobile/lib/repositories/auth_repository.dart`
- Test: `lazyclaw/mobile/test/repositories/auth_repository_test.dart`

- [ ] **Step 1: Write the failing test (mock the ApiClient via a thin seam)**

Create `test/repositories/auth_repository_test.dart`:
```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/auth_repository.dart';
import 'package:lazyclaw_mobile/models/user.dart';

class _FakeTransport implements AuthTransport {
  Map<String, dynamic>? lastBody;
  String? lastPath;
  final Map<String, dynamic> response;
  _FakeTransport(this.response);
  @override
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body) async {
    lastPath = path; lastBody = body; return response;
  }
}

void main() {
  test('login posts credentials and parses the user', () async {
    final t = _FakeTransport(
        {'id': 'u1', 'username': 'sam', 'display_name': 'Sam', 'role': 'user'});
    final repo = AuthRepository(t);
    final result = await repo.login('sam', 'pw12345678');
    expect(t.lastPath, '/api/auth/login');
    expect(t.lastBody, {'username': 'sam', 'password': 'pw12345678'});
    expect(result, isA<User>());
    expect((result as User).username, 'sam');
  });
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `flutter test test/repositories/auth_repository_test.dart` — Expected: FAIL (no `auth_repository.dart`).

- [ ] **Step 3: Implement with a transport seam (so it's testable without Dio)**

Create `lib/repositories/auth_repository.dart`:
```dart
import '../core/api/api_client.dart';
import '../models/user.dart';

/// Minimal seam so the repository is unit-testable without a live Dio.
abstract class AuthTransport {
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body);
}

class DioAuthTransport implements AuthTransport {
  final ApiClient _client;
  DioAuthTransport(this._client);
  @override
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body) =>
      _client.post<Map<String, dynamic>>(path, data: body,
          fromJson: (d) => Map<String, dynamic>.from(d as Map));
}

class AuthRepository {
  final AuthTransport _t;
  AuthRepository(this._t);

  Future<User> login(String username, String password) async {
    final json = await _t.postJson('/api/auth/login',
        {'username': username, 'password': password});
    return User.fromJson(json);
  }

  Future<Map<String, dynamic>> register(
      String username, String password,
      {String? displayName, String? inviteToken}) async {
    return _t.postJson('/api/auth/register', {
      'username': username,
      'password': password,
      'display_name': displayName,
      'invite_token': inviteToken,
    });
  }

  Future<User> me() async {
    final json = await _t.postJson('/api/auth/me', const {});
    return User.fromJson(json);
  }
}
```
Note: `me()` is GET on the server, but the seam keeps a single method shape for testing; the Dio transport for `me` is added in Task 7 where the live client wires GET. For Milestone A, `me()` is only used post-login session-check and can route through a GET helper in `DioAuthTransport` — see Task 7 Step 3.

- [ ] **Step 4: Run to verify it passes**

Run: `flutter test test/repositories/auth_repository_test.dart` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/repositories mobile/test/repositories
git commit -m "feat(mobile): auth repository with testable transport seam"
```

---

## Task 7: Auth provider (Riverpod state machine + on401→logout)

**Files:**
- Create: `lazyclaw/mobile/lib/providers/auth_provider.dart`
- Modify: `lazyclaw/mobile/lib/repositories/auth_repository.dart` (add GET `me` to the Dio transport)
- Test: `lazyclaw/mobile/test/providers/auth_provider_test.dart`

- [ ] **Step 1: Add a GET path to the Dio transport for `me`**

In `lib/repositories/auth_repository.dart`, extend `DioAuthTransport`:
```dart
class DioAuthTransport implements AuthTransport {
  final ApiClient _client;
  DioAuthTransport(this._client);
  @override
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body) {
    if (path == '/api/auth/me') {
      return _client.get<Map<String, dynamic>>(path,
          fromJson: (d) => Map<String, dynamic>.from(d as Map));
    }
    return _client.post<Map<String, dynamic>>(path, data: body,
        fromJson: (d) => Map<String, dynamic>.from(d as Map));
  }
}
```

- [ ] **Step 2: Write the failing test for state transitions**

Create `test/providers/auth_provider_test.dart`:
```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/auth_provider.dart';
import 'package:lazyclaw_mobile/repositories/auth_repository.dart';
import 'package:lazyclaw_mobile/models/user.dart';

class _OkTransport implements AuthTransport {
  @override
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body) async =>
      {'id': 'u1', 'username': 'sam', 'role': 'user'};
}

void main() {
  test('login moves unauthenticated -> authenticated', () async {
    final notifier = AuthNotifier(AuthRepository(_OkTransport()));
    expect(notifier.state.status, AuthStatus.unauthenticated);
    final err = await notifier.login('sam', 'pw12345678');
    expect(err, isNull);
    expect(notifier.state.status, AuthStatus.authenticated);
    expect(notifier.state.user, isA<User>());
  });

  test('handle401 forces logout', () {
    final notifier = AuthNotifier(AuthRepository(_OkTransport()));
    notifier.state = AuthState.authenticated(
        const User(id: 'u1', username: 'sam', role: 'user'));
    notifier.handle401();
    expect(notifier.state.status, AuthStatus.unauthenticated);
  });
}
```

- [ ] **Step 3: Run to verify it fails**

Run: `flutter test test/providers/auth_provider_test.dart` — Expected: FAIL (no `auth_provider.dart`).

- [ ] **Step 4: Implement the provider**

Create `lib/providers/auth_provider.dart`:
```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api/api_client.dart';
import '../core/api/api_exceptions.dart';
import '../core/config/server_config.dart';
import '../models/user.dart';
import '../repositories/auth_repository.dart';

enum AuthStatus { loading, authenticated, unauthenticated }

class AuthState {
  final AuthStatus status;
  final User? user;
  const AuthState(this.status, this.user);
  factory AuthState.loading() => const AuthState(AuthStatus.loading, null);
  factory AuthState.authenticated(User u) =>
      AuthState(AuthStatus.authenticated, u);
  factory AuthState.unauthenticated() =>
      const AuthState(AuthStatus.unauthenticated, null);
}

final apiClientProvider = Provider<ApiClient>((ref) {
  // baseUrl is resolved asynchronously by bootstrap (Task 13); default for now.
  return ApiClient(baseUrl: ref.watch(baseUrlProvider));
});

final baseUrlProvider = StateProvider<String>((ref) => 'http://127.0.0.1:18789');

final authRepositoryProvider = Provider<AuthRepository>((ref) =>
    AuthRepository(DioAuthTransport(ref.watch(apiClientProvider))));

class AuthNotifier extends StateNotifier<AuthState> {
  final AuthRepository _repo;
  AuthNotifier(this._repo) : super(AuthState.unauthenticated());

  void handle401() => state = AuthState.unauthenticated();

  Future<String?> login(String username, String password) async {
    try {
      final user = await _repo.login(username, password);
      state = AuthState.authenticated(user);
      return null;
    } on ApiError catch (e) {
      return e.message;
    } catch (e) {
      return e.toString();
    }
  }

  Future<String?> register(String username, String password,
      {String? displayName, String? inviteToken}) async {
    try {
      await _repo.register(username, password,
          displayName: displayName, inviteToken: inviteToken);
      return await login(username, password);
    } on ApiError catch (e) {
      return e.message;
    } catch (e) {
      return e.toString();
    }
  }

  Future<void> checkSession() async {
    state = AuthState.loading();
    try {
      final user = await _repo.me();
      state = AuthState.authenticated(user);
    } catch (_) {
      state = AuthState.unauthenticated();
    }
  }

  Future<void> logout() async {
    state = AuthState.unauthenticated();
  }
}

final authProvider =
    StateNotifierProvider<AuthNotifier, AuthState>((ref) {
  final notifier = AuthNotifier(ref.watch(authRepositoryProvider));
  // Wire the transport's 401 hook to force logout.
  ref.watch(apiClientProvider).on401 = notifier.handle401;
  return notifier;
});
```

- [ ] **Step 5: Run to verify it passes**

Run: `flutter test test/providers/auth_provider_test.dart` — Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mobile/lib/providers mobile/lib/repositories mobile/test/providers
git commit -m "feat(mobile): auth provider state machine + on401 logout"
```

---

## Task 8: Chat WS frame parser (pure, fully unit-tested)

**Files:**
- Create: `lazyclaw/mobile/lib/chat/ws_frames.dart`
- Test: `lazyclaw/mobile/test/chat/ws_frames_test.dart`

- [ ] **Step 1: Write the failing test for every frame type Milestone A handles**

Create `test/chat/ws_frames_test.dart`:
```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/ws_frames.dart';

void main() {
  test('parses token frame', () {
    final f = parseServerFrame('{"type":"token","content":"hel"}');
    expect(f, isA<TokenFrame>());
    expect((f as TokenFrame).content, 'hel');
  });

  test('parses done frame with content', () {
    final f = parseServerFrame(
        '{"type":"done","content":"final reply","model_used":"claude"}');
    expect(f, isA<DoneFrame>());
    expect((f as DoneFrame).content, 'final reply');
  });

  test('parses error frame', () {
    final f = parseServerFrame('{"type":"error","message":"boom"}');
    expect((f as ErrorFrame).message, 'boom');
  });

  test('parses approval_request frame', () {
    final f = parseServerFrame(
        '{"type":"approval_request","request_id":"abc123","skill":"send_email","args":{"to":"x"}}');
    expect(f, isA<ApprovalRequestFrame>());
    expect((f as ApprovalRequestFrame).requestId, 'abc123');
    expect(f.skill, 'send_email');
  });

  test('unknown type -> UnknownFrame (never throws)', () {
    final f = parseServerFrame('{"type":"specialist_thinking","x":1}');
    expect(f, isA<UnknownFrame>());
    expect((f as UnknownFrame).type, 'specialist_thinking');
  });

  test('malformed json -> UnknownFrame', () {
    final f = parseServerFrame('not json');
    expect(f, isA<UnknownFrame>());
  });

  test('encodes a client message frame', () {
    expect(encodeClientMessage('hello'),
        '{"type":"message","content":"hello","session_id":null}');
  });

  test('encodes approval response', () {
    expect(encodeApprovalResponse('abc123', true),
        '{"type":"approval_response","request_id":"abc123","approved":true}');
  });
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `flutter test test/chat/ws_frames_test.dart` — Expected: FAIL (no `ws_frames.dart`).

- [ ] **Step 3: Implement the parser/encoder**

Create `lib/chat/ws_frames.dart`:
```dart
import 'dart:convert';

sealed class ServerFrame {
  const ServerFrame();
}

class TokenFrame extends ServerFrame {
  final String content;
  const TokenFrame(this.content);
}

class DoneFrame extends ServerFrame {
  final String content;
  final String? modelUsed;
  const DoneFrame(this.content, this.modelUsed);
}

class ErrorFrame extends ServerFrame {
  final String message;
  const ErrorFrame(this.message);
}

class CancelledFrame extends ServerFrame {
  const CancelledFrame();
}

class ApprovalRequestFrame extends ServerFrame {
  final String requestId;
  final String skill;
  final Map<String, dynamic> args;
  const ApprovalRequestFrame(this.requestId, this.skill, this.args);
}

class UnknownFrame extends ServerFrame {
  final String type;
  const UnknownFrame(this.type);
}

ServerFrame parseServerFrame(String raw) {
  try {
    final m = jsonDecode(raw);
    if (m is! Map) return const UnknownFrame('');
    final type = (m['type'] as String?) ?? '';
    switch (type) {
      case 'token':
        return TokenFrame((m['content'] as String?) ?? '');
      case 'done':
        return DoneFrame(
            (m['content'] as String?) ?? '', m['model_used'] as String?);
      case 'error':
        return ErrorFrame((m['message'] as String?) ?? 'unknown error');
      case 'cancelled':
        return const CancelledFrame();
      case 'approval_request':
        return ApprovalRequestFrame(
          (m['request_id'] as String?) ?? '',
          (m['skill'] as String?) ?? '',
          (m['args'] is Map)
              ? Map<String, dynamic>.from(m['args'] as Map)
              : const {},
        );
      default:
        return UnknownFrame(type);
    }
  } catch (_) {
    return const UnknownFrame('');
  }
}

String encodeClientMessage(String content, {String? sessionId}) =>
    jsonEncode({'type': 'message', 'content': content, 'session_id': sessionId});

String encodeApprovalResponse(String requestId, bool approved) => jsonEncode(
    {'type': 'approval_response', 'request_id': requestId, 'approved': approved});

String encodePing() => jsonEncode({'type': 'ping'});
String encodeCancel() => jsonEncode({'type': 'cancel'});
```

- [ ] **Step 4: Run to verify it passes**

Run: `flutter test test/chat/ws_frames_test.dart` — Expected: PASS (all 8 tests).

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/chat/ws_frames.dart mobile/test/chat/ws_frames_test.dart
git commit -m "feat(mobile): chat ws frame parser + client encoders"
```

---

## Task 9: Chat socket service (web_socket_channel, cookie-authed)

**Files:**
- Create: `lazyclaw/mobile/lib/chat/chat_socket.dart`
- Test: `lazyclaw/mobile/test/chat/chat_socket_test.dart`

- [ ] **Step 1: Write the failing test (inject a fake channel factory)**

Create `test/chat/chat_socket_test.dart`:
```dart
import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/chat_socket.dart';
import 'package:lazyclaw_mobile/chat/ws_frames.dart';

void main() {
  test('emits parsed frames from the underlying socket', () async {
    final incoming = StreamController<dynamic>();
    final sent = <String>[];
    final socket = ChatSocket(
      channelFactory: (url, headers) => FakeSink(incoming.stream, sent),
    );
    final frames = <ServerFrame>[];
    socket.frames.listen(frames.add);
    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');

    incoming.add('{"type":"token","content":"hi"}');
    incoming.add('{"type":"done","content":"hi there"}');
    await Future<void>.delayed(Duration.zero);

    expect(frames.whereType<TokenFrame>().length, 1);
    expect(frames.whereType<DoneFrame>().length, 1);

    socket.send('hello');
    expect(sent.last, contains('"type":"message"'));
    await incoming.close();
  });
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `flutter test test/chat/chat_socket_test.dart` — Expected: FAIL (no `chat_socket.dart`).

- [ ] **Step 3: Implement with a `WebSocketSink` seam**

Create `lib/chat/chat_socket.dart`:
```dart
import 'dart:async';
import 'package:web_socket_channel/io.dart';
import 'ws_frames.dart';

/// Minimal seam so the socket is unit-testable without a real server.
abstract class WsSink {
  Stream<dynamic> get stream;
  void add(String data);
  Future<void> close();
}

class FakeSink implements WsSink {
  @override
  final Stream<dynamic> stream;
  final List<String> sent;
  FakeSink(this.stream, this.sent);
  @override
  void add(String data) => sent.add(data);
  @override
  Future<void> close() async {}
}

class _IoSink implements WsSink {
  final IOWebSocketChannel _ch;
  _IoSink(this._ch);
  @override
  Stream<dynamic> get stream => _ch.stream;
  @override
  void add(String data) => _ch.sink.add(data);
  @override
  Future<void> close() async => _ch.sink.close();
}

typedef ChannelFactory = WsSink Function(String url, Map<String, String> headers);

class ChatSocket {
  final ChannelFactory _factory;
  final _frames = StreamController<ServerFrame>.broadcast();
  WsSink? _sink;
  Timer? _ping;

  ChatSocket({ChannelFactory? channelFactory})
      : _factory = channelFactory ??
            ((url, headers) =>
                _IoSink(IOWebSocketChannel.connect(url, headers: headers)));

  Stream<ServerFrame> get frames => _frames.stream;

  Future<void> connect(String wsUrl, {required String cookie}) async {
    // IMPORTANT: send the session cookie, and DO NOT send an Origin header
    // (native client → server allows absent Origin; presence triggers CORS).
    final sink = _factory(wsUrl, {'Cookie': cookie});
    _sink = sink;
    sink.stream.listen(
      (data) => _frames.add(parseServerFrame(data.toString())),
      onError: (e) => _frames.add(ErrorFrame(e.toString())),
      onDone: () => _ping?.cancel(),
    );
    _ping = Timer.periodic(
        const Duration(seconds: 30), (_) => _sink?.add(encodePing()));
  }

  void send(String content) => _sink?.add(encodeClientMessage(content));
  void approve(String requestId, bool approved) =>
      _sink?.add(encodeApprovalResponse(requestId, approved));
  void cancel() => _sink?.add(encodeCancel());

  Future<void> dispose() async {
    _ping?.cancel();
    await _sink?.close();
    await _frames.close();
  }
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `flutter test test/chat/chat_socket_test.dart` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/chat/chat_socket.dart mobile/test/chat/chat_socket_test.dart
git commit -m "feat(mobile): cookie-authed chat websocket with testable seam"
```

---

## Task 10: Chat message model + controller (frames → message list)

**Files:**
- Create: `lazyclaw/mobile/lib/chat/chat_message.dart`
- Create: `lazyclaw/mobile/lib/chat/chat_controller.dart`
- Test: `lazyclaw/mobile/test/chat/chat_controller_test.dart`

- [ ] **Step 1: Message model**

Create `lib/chat/chat_message.dart`:
```dart
class ChatMessage {
  final String role; // 'user' | 'assistant'
  final String content;
  final bool streaming;
  final String? pendingApprovalId;
  final String? pendingApprovalSkill;
  const ChatMessage({
    required this.role,
    required this.content,
    this.streaming = false,
    this.pendingApprovalId,
    this.pendingApprovalSkill,
  });

  ChatMessage copyWith({String? content, bool? streaming}) => ChatMessage(
        role: role,
        content: content ?? this.content,
        streaming: streaming ?? this.streaming,
        pendingApprovalId: pendingApprovalId,
        pendingApprovalSkill: pendingApprovalSkill,
      );
}
```

- [ ] **Step 2: Write the failing test for the controller's reduce logic**

Create `test/chat/chat_controller_test.dart`:
```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/chat_controller.dart';
import 'package:lazyclaw_mobile/chat/ws_frames.dart';

void main() {
  test('user send then streamed tokens then done builds two messages', () {
    final c = ChatReducer();
    c.onUserSend('hello');
    expect(c.messages.length, 2); // user + empty streaming assistant
    expect(c.messages.first.role, 'user');
    expect(c.messages.last.streaming, isTrue);

    c.onFrame(const TokenFrame('Hi '));
    c.onFrame(const TokenFrame('there'));
    expect(c.messages.last.content, 'Hi there');

    c.onFrame(const DoneFrame('Hi there', 'claude'));
    expect(c.messages.last.streaming, isFalse);
    expect(c.messages.last.content, 'Hi there');
  });

  test('done with empty content keeps streamed buffer', () {
    final c = ChatReducer();
    c.onUserSend('x');
    c.onFrame(const TokenFrame('buffered'));
    c.onFrame(const DoneFrame('', null));
    expect(c.messages.last.content, 'buffered');
  });

  test('approval_request surfaces a pending approval on the assistant msg', () {
    final c = ChatReducer();
    c.onUserSend('do it');
    c.onFrame(const ApprovalRequestFrame('req1', 'send_email', {}));
    expect(c.messages.last.pendingApprovalId, 'req1');
    expect(c.messages.last.pendingApprovalSkill, 'send_email');
  });
}
```

- [ ] **Step 3: Run to verify it fails**

Run: `flutter test test/chat/chat_controller_test.dart` — Expected: FAIL.

- [ ] **Step 4: Implement the reducer + Riverpod controller**

Create `lib/chat/chat_controller.dart`:
```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'chat_message.dart';
import 'chat_socket.dart';
import 'ws_frames.dart';

/// Pure reducer (no IO) so the frame→messages logic is unit-tested.
class ChatReducer {
  final List<ChatMessage> messages = [];
  final StringBuffer _buf = StringBuffer();

  void onUserSend(String text) {
    messages.add(ChatMessage(role: 'user', content: text));
    _buf.clear();
    messages.add(const ChatMessage(role: 'assistant', content: '', streaming: true));
  }

  void onFrame(ServerFrame f) {
    switch (f) {
      case TokenFrame(:final content):
        _buf.write(content);
        _replaceLast(messages.last.copyWith(content: _buf.toString()));
      case DoneFrame(:final content):
        final finalText = content.isNotEmpty ? content : _buf.toString();
        _replaceLast(
            messages.last.copyWith(content: finalText, streaming: false));
      case ErrorFrame(:final message):
        _replaceLast(messages.last
            .copyWith(content: '⚠️ $message', streaming: false));
      case CancelledFrame():
        _replaceLast(messages.last.copyWith(streaming: false));
      case ApprovalRequestFrame(:final requestId, :final skill):
        final last = messages.last;
        messages[messages.length - 1] = ChatMessage(
          role: last.role,
          content: last.content,
          streaming: last.streaming,
          pendingApprovalId: requestId,
          pendingApprovalSkill: skill,
        );
      case UnknownFrame():
        break; // ignored in Milestone A
    }
  }

  void _replaceLast(ChatMessage m) => messages[messages.length - 1] = m;
}

class ChatController extends StateNotifier<List<ChatMessage>> {
  final ChatSocket _socket;
  final ChatReducer _reducer = ChatReducer();
  ChatController(this._socket) : super(const []) {
    _socket.frames.listen((f) {
      _reducer.onFrame(f);
      state = List.unmodifiable(_reducer.messages);
    });
  }

  void send(String text) {
    _reducer.onUserSend(text);
    state = List.unmodifiable(_reducer.messages);
    _socket.send(text);
  }

  void respondApproval(String id, bool approved) =>
      _socket.approve(id, approved);
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `flutter test test/chat/chat_controller_test.dart` — Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add mobile/lib/chat/chat_message.dart mobile/lib/chat/chat_controller.dart mobile/test/chat/chat_controller_test.dart
git commit -m "feat(mobile): chat reducer + controller (frames -> messages)"
```

---

## Task 11: Theme harvest

**Files:**
- Create: `lazyclaw/mobile/lib/core/theme/app_theme.dart` (from donor)

- [ ] **Step 1: Copy + de-taskbot the theme**

Run:
```bash
cd ~/Desktop/Code_Projects/lazyclaw/mobile && mkdir -p lib/core/theme
cp ~/Desktop/Code_Projects/taskbot/taskbot_flutter/lib/core/theme/app_theme.dart lib/core/theme/app_theme.dart
grep -rn "taskbot_flutter" lib/core/theme/ && sed -i '' 's/taskbot_flutter/lazyclaw_mobile/g' lib/core/theme/app_theme.dart || echo "clean"
```

- [ ] **Step 2: Verify it analyzes**

Run: `cd ~/Desktop/Code_Projects/lazyclaw/mobile && flutter analyze lib/core/theme/app_theme.dart 2>&1 | tail -3`
Expected: `No issues found!` (or only style infos). If `buildTheme(String)` references a constant map, keep it.

- [ ] **Step 3: Commit**

```bash
git add mobile/lib/core/theme
git commit -m "feat(mobile): harvest dark theme"
```

---

## Task 12: Screens — server setup, login, register, chat

**Files:**
- Create: `lazyclaw/mobile/lib/screens/server_setup_screen.dart`
- Create: `lazyclaw/mobile/lib/screens/login_screen.dart`
- Create: `lazyclaw/mobile/lib/screens/register_screen.dart`
- Create: `lazyclaw/mobile/lib/screens/chat_screen.dart`
- Test: `lazyclaw/mobile/test/screens/login_screen_test.dart`

- [ ] **Step 1: Server setup screen (enter the self-hosted gateway URL)**

Create `lib/screens/server_setup_screen.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/config/server_config.dart';
import '../providers/auth_provider.dart';

class ServerSetupScreen extends ConsumerStatefulWidget {
  const ServerSetupScreen({super.key});
  @override
  ConsumerState<ServerSetupScreen> createState() => _ServerSetupScreenState();
}

class _ServerSetupScreenState extends ConsumerState<ServerSetupScreen> {
  final _ctrl = TextEditingController();
  @override
  void initState() {
    super.initState();
    ServerConfig.load().then((v) => _ctrl.text = v);
  }
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Connect to your LazyClaw')),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(children: [
          const Text('Enter your computer\'s address (e.g. 192.168.1.5:18789)'),
          const SizedBox(height: 12),
          TextField(controller: _ctrl,
              decoration: const InputDecoration(labelText: 'Gateway URL')),
          const SizedBox(height: 20),
          FilledButton(
            onPressed: () async {
              final url = ServerConfig.normalizeBaseUrl(_ctrl.text);
              await ServerConfig.save(url);
              ref.read(baseUrlProvider.notifier).state = url;
              if (context.mounted) Navigator.pop(context);
            },
            child: const Text('Save'),
          ),
        ]),
      ),
    );
  }
}
```

- [ ] **Step 2: Login screen**

Create `lib/screens/login_screen.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../providers/auth_provider.dart';
import 'server_setup_screen.dart';

class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});
  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final _user = TextEditingController();
  final _pass = TextEditingController();
  String? _error;
  bool _busy = false;

  Future<void> _submit() async {
    setState(() { _busy = true; _error = null; });
    final err = await ref.read(authProvider.notifier)
        .login(_user.text.trim(), _pass.text);
    if (mounted) setState(() { _busy = false; _error = err; });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('LazyClaw'), actions: [
        IconButton(
          icon: const Icon(Icons.dns),
          tooltip: 'Server',
          onPressed: () => Navigator.push(context,
              MaterialPageRoute(builder: (_) => const ServerSetupScreen())),
        ),
      ]),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(children: [
          TextField(controller: _user, key: const Key('login_user'),
              decoration: const InputDecoration(labelText: 'Username')),
          TextField(controller: _pass, key: const Key('login_pass'),
              obscureText: true,
              decoration: const InputDecoration(labelText: 'Password')),
          if (_error != null)
            Padding(padding: const EdgeInsets.only(top: 12),
                child: Text(_error!, style: const TextStyle(color: Colors.red))),
          const SizedBox(height: 20),
          FilledButton(
            key: const Key('login_submit'),
            onPressed: _busy ? null : _submit,
            child: _busy
                ? const CircularProgressIndicator()
                : const Text('Log in'),
          ),
          TextButton(
            onPressed: () => Navigator.pushNamed(context, '/register'),
            child: const Text('Create account'),
          ),
        ]),
      ),
    );
  }
}
```

- [ ] **Step 3: Register screen**

Create `lib/screens/register_screen.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../providers/auth_provider.dart';

class RegisterScreen extends ConsumerStatefulWidget {
  const RegisterScreen({super.key});
  @override
  ConsumerState<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends ConsumerState<RegisterScreen> {
  final _user = TextEditingController();
  final _pass = TextEditingController();
  final _invite = TextEditingController();
  String? _error;
  bool _busy = false;

  Future<void> _submit() async {
    setState(() { _busy = true; _error = null; });
    final err = await ref.read(authProvider.notifier).register(
          _user.text.trim(), _pass.text,
          inviteToken: _invite.text.trim().isEmpty ? null : _invite.text.trim(),
        );
    if (mounted) setState(() { _busy = false; _error = err; });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Create account')),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(children: [
          TextField(controller: _user,
              decoration: const InputDecoration(labelText: 'Username')),
          TextField(controller: _pass, obscureText: true,
              decoration: const InputDecoration(labelText: 'Password (min 8)')),
          TextField(controller: _invite,
              decoration:
                  const InputDecoration(labelText: 'Invite token (if required)')),
          if (_error != null)
            Padding(padding: const EdgeInsets.only(top: 12),
                child: Text(_error!, style: const TextStyle(color: Colors.red))),
          const SizedBox(height: 20),
          FilledButton(
              onPressed: _busy ? null : _submit,
              child: _busy
                  ? const CircularProgressIndicator()
                  : const Text('Register')),
        ]),
      ),
    );
  }
}
```

- [ ] **Step 4: Chat screen**

Create `lib/screens/chat_screen.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import '../chat/chat_controller.dart';
import '../chat/chat_socket.dart';
import '../chat/chat_message.dart';
import '../core/config/server_config.dart';
import '../core/api/api_client.dart';
import '../providers/auth_provider.dart';

final chatSocketProvider = Provider<ChatSocket>((ref) => ChatSocket());

final chatControllerProvider =
    StateNotifierProvider<ChatController, List<ChatMessage>>(
        (ref) => ChatController(ref.watch(chatSocketProvider)));

class ChatScreen extends ConsumerStatefulWidget {
  const ChatScreen({super.key});
  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  final _input = TextEditingController();
  bool _connected = false;

  @override
  void initState() {
    super.initState();
    _connect();
  }

  Future<void> _connect() async {
    final base = ref.read(baseUrlProvider);
    final cookie = await ref.read(apiClientProvider).getSessionCookie();
    if (cookie == null) return;
    await ref.read(chatSocketProvider).connect(
          ServerConfig.wsUrlFor(base),
          cookie: 'session_id=$cookie',
        );
    if (mounted) setState(() => _connected = true);
  }

  @override
  Widget build(BuildContext context) {
    final messages = ref.watch(chatControllerProvider);
    return Scaffold(
      appBar: AppBar(
        title: Text(_connected ? 'Chat' : 'Connecting…'),
        actions: [
          IconButton(
            icon: const Icon(Icons.logout),
            onPressed: () => ref.read(authProvider.notifier).logout(),
          ),
        ],
      ),
      body: Column(children: [
        Expanded(
          child: ListView.builder(
            reverse: true,
            padding: const EdgeInsets.all(12),
            itemCount: messages.length,
            itemBuilder: (c, i) {
              final m = messages[messages.length - 1 - i];
              return _Bubble(m, onApprove: (id, ok) =>
                  ref.read(chatControllerProvider.notifier).respondApproval(id, ok));
            },
          ),
        ),
        SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(8),
            child: Row(children: [
              Expanded(
                child: TextField(controller: _input,
                    decoration:
                        const InputDecoration(hintText: 'Message LazyClaw…')),
              ),
              IconButton(
                icon: const Icon(Icons.send),
                onPressed: () {
                  final t = _input.text.trim();
                  if (t.isEmpty) return;
                  ref.read(chatControllerProvider.notifier).send(t);
                  _input.clear();
                },
              ),
            ]),
          ),
        ),
      ]),
    );
  }
}

class _Bubble extends StatelessWidget {
  final ChatMessage m;
  final void Function(String id, bool approved) onApprove;
  const _Bubble(this.m, {required this.onApprove});
  @override
  Widget build(BuildContext context) {
    final isUser = m.role == 'user';
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * 0.8),
        decoration: BoxDecoration(
          color: isUser
              ? Theme.of(context).colorScheme.primary
              : Theme.of(context).colorScheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          isUser
              ? Text(m.content,
                  style: const TextStyle(color: Colors.white))
              : MarkdownBody(data: m.content.isEmpty ? '…' : m.content),
          if (m.pendingApprovalId != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Row(children: [
                Text('Approve ${m.pendingApprovalSkill}?'),
                const Spacer(),
                TextButton(
                    onPressed: () => onApprove(m.pendingApprovalId!, false),
                    child: const Text('Deny')),
                FilledButton(
                    onPressed: () => onApprove(m.pendingApprovalId!, true),
                    child: const Text('Approve')),
              ]),
            ),
        ]),
      ),
    );
  }
}
```

- [ ] **Step 5: Write a widget test for the login screen**

Create `test/screens/login_screen_test.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/screens/login_screen.dart';

void main() {
  testWidgets('login screen renders fields and submit', (tester) async {
    await tester.pumpWidget(const ProviderScope(
        child: MaterialApp(home: LoginScreen())));
    expect(find.byKey(const Key('login_user')), findsOneWidget);
    expect(find.byKey(const Key('login_pass')), findsOneWidget);
    expect(find.byKey(const Key('login_submit')), findsOneWidget);
  });
}
```

- [ ] **Step 6: Run the widget test**

Run: `flutter test test/screens/login_screen_test.dart` — Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add mobile/lib/screens mobile/test/screens
git commit -m "feat(mobile): server-setup, login, register, chat screens"
```

---

## Task 13: Router + app entry (bootstrap base URL, wire auth guard)

**Files:**
- Create: `lazyclaw/mobile/lib/core/router/app_router.dart`
- Modify: `lazyclaw/mobile/lib/main.dart`

- [ ] **Step 1: Router with auth-guard redirect**

Create `lib/core/router/app_router.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../providers/auth_provider.dart';
import '../../screens/login_screen.dart';
import '../../screens/register_screen.dart';
import '../../screens/chat_screen.dart';

final routerProvider = Provider<GoRouter>((ref) {
  return GoRouter(
    initialLocation: '/chat',
    redirect: (context, state) {
      final status = ref.read(authProvider).status;
      final loggingIn = state.matchedLocation == '/login' ||
          state.matchedLocation == '/register';
      if (status == AuthStatus.loading) return null;
      final authed = status == AuthStatus.authenticated;
      if (!authed && !loggingIn) return '/login';
      if (authed && loggingIn) return '/chat';
      return null;
    },
    routes: [
      GoRoute(path: '/login', builder: (_, __) => const LoginScreen()),
      GoRoute(path: '/register', builder: (_, __) => const RegisterScreen()),
      GoRoute(path: '/chat', builder: (_, __) => const ChatScreen()),
    ],
  );
});
```

- [ ] **Step 2: App entry — load base URL, check session, run**

Replace `lib/main.dart`:
```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'core/config/server_config.dart';
import 'core/router/app_router.dart';
import 'core/theme/app_theme.dart';
import 'providers/auth_provider.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final baseUrl = await ServerConfig.load();
  runApp(ProviderScope(
    overrides: [baseUrlProvider.overrideWith((ref) => baseUrl)],
    child: const LazyClawApp(),
  ));
}

class LazyClawApp extends ConsumerStatefulWidget {
  const LazyClawApp({super.key});
  @override
  ConsumerState<LazyClawApp> createState() => _LazyClawAppState();
}

class _LazyClawAppState extends ConsumerState<LazyClawApp> {
  @override
  void initState() {
    super.initState();
    Future.microtask(() => ref.read(authProvider.notifier).checkSession());
  }
  @override
  Widget build(BuildContext context) {
    final router = ref.watch(routerProvider);
    return MaterialApp.router(
      title: 'LazyClaw',
      theme: buildTheme('blue'),
      routerConfig: router,
    );
  }
}
```
Note: if the harvested `buildTheme` requires a different default id, use one of its `kThemeColors` keys.

- [ ] **Step 3: Analyze + build a debug APK to prove the whole app compiles**

Run:
```bash
cd ~/Desktop/Code_Projects/lazyclaw/mobile && flutter analyze 2>&1 | tail -5 && flutter build apk --debug 2>&1 | tail -3
```
Expected: analyze shows no errors (style infos OK); `✓ Built ...app-debug.apk`.

- [ ] **Step 4: Run the full test suite**

Run: `flutter test 2>&1 | tail -5` — Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add mobile/lib/core/router mobile/lib/main.dart
git commit -m "feat(mobile): router auth-guard + app bootstrap"
```

---

## Task 14: Backend — serve the APK + version

**Files:**
- Create: `lazyclaw/lazyclaw/gateway/routes/mobile.py`
- Modify: the gateway app where routers are registered (find with the command in Step 3)
- Test: `lazyclaw/tests/gateway/test_mobile_routes.py`

- [ ] **Step 1: Write the failing test**

Create `tests/gateway/test_mobile_routes.py`:
```python
import json
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.gateway.routes.mobile import router, set_apk_dir


def _app():
    app = FastAPI()
    app.include_router(router)
    return app


def test_version_404_when_absent(tmp_path):
    set_apk_dir(tmp_path)
    client = TestClient(_app())
    assert client.get("/api/mobile/version").status_code == 404


def test_version_and_apk_served(tmp_path):
    set_apk_dir(tmp_path)
    (tmp_path / "app-release.apk").write_bytes(b"PK\x03\x04 fake apk")
    (tmp_path / "version.json").write_text(json.dumps(
        {"version": "0.1.0", "build": 1, "sha256": "abc", "built_at": "x"}))
    client = TestClient(_app())

    v = client.get("/api/mobile/version")
    assert v.status_code == 200
    assert v.json()["version"] == "0.1.0"

    a = client.get("/api/mobile/apk")
    assert a.status_code == 200
    assert a.headers["content-type"] == "application/vnd.android.package-archive"
    assert a.content.startswith(b"PK")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Desktop/Code_Projects/lazyclaw && python -m pytest tests/gateway/test_mobile_routes.py -q`
Expected: FAIL — `lazyclaw.gateway.routes.mobile` does not exist.

- [ ] **Step 3: Find how routers are registered**

Run:
```bash
cd ~/Desktop/Code_Projects/lazyclaw && grep -rn "include_router" lazyclaw/gateway | head
```
Note the file + pattern (e.g. `lazyclaw/gateway/app.py`). You will mirror it in Step 5.

- [ ] **Step 4: Implement the routes**

Create `lazyclaw/gateway/routes/mobile.py`:
```python
"""Serve the sideloadable Android APK + its version metadata."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter(prefix="/api/mobile", tags=["mobile"])

# Default served location; overridable for tests.
_APK_DIR = Path(__file__).resolve().parents[3] / "mobile" / "dist"


def set_apk_dir(path: Path) -> None:
    global _APK_DIR
    _APK_DIR = Path(path)


def _apk_path() -> Path:
    return _APK_DIR / "app-release.apk"


def _version_path() -> Path:
    return _APK_DIR / "version.json"


@router.get("/version")
async def mobile_version() -> JSONResponse:
    vp = _version_path()
    if not vp.exists() or not _apk_path().exists():
        raise HTTPException(status_code=404, detail="No mobile build published")
    return JSONResponse(json.loads(vp.read_text()))


@router.get("/apk")
async def mobile_apk() -> FileResponse:
    ap = _apk_path()
    if not ap.exists():
        raise HTTPException(status_code=404, detail="No APK published")
    return FileResponse(
        ap,
        media_type="application/vnd.android.package-archive",
        filename="lazyclaw.apk",
    )
```
Note: Milestone A serves these without an auth dependency so the phone (pre-login) can fetch via the QR. Harden with a signed token in a later phase (spec §14.1).

- [ ] **Step 5: Register the router**

In the gateway app file found in Step 3, add (mirroring the existing pattern):
```python
from lazyclaw.gateway.routes import mobile as mobile_routes
app.include_router(mobile_routes.router)
```

- [ ] **Step 6: Run to verify it passes**

Run: `python -m pytest tests/gateway/test_mobile_routes.py -q` — Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add lazyclaw/gateway/routes/mobile.py tests/gateway/test_mobile_routes.py
# plus the modified gateway app file from Step 5
git commit -m "feat(gateway): serve mobile apk + version"
```

---

## Task 15: Build script — produce + stamp the APK into the served dir

**Files:**
- Create: `lazyclaw/scripts/build-mobile-apk.sh`

- [ ] **Step 1: Write the build script**

Create `lazyclaw/scripts/build-mobile-apk.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MOBILE="$ROOT/mobile"
DIST="$MOBILE/dist"
mkdir -p "$DIST"

cd "$MOBILE"
flutter build apk --release

SRC="$MOBILE/build/app/outputs/flutter-apk/app-release.apk"
cp "$SRC" "$DIST/app-release.apk"

VER="$(grep '^version:' "$MOBILE/pubspec.yaml" | awk '{print $2}')"
NAME="${VER%%+*}"; BUILD="${VER##*+}"
SHA="$(shasum -a 256 "$DIST/app-release.apk" | awk '{print $1}')"
BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > "$DIST/version.json" <<EOF
{"version":"$NAME","build":$BUILD,"sha256":"$SHA","built_at":"$BUILT_AT","min_android":"7.0"}
EOF
echo "Published $DIST/app-release.apk (v$NAME+$BUILD, sha256 $SHA)"
```

- [ ] **Step 2: Make it executable and run it**

Run:
```bash
chmod +x ~/Desktop/Code_Projects/lazyclaw/scripts/build-mobile-apk.sh
~/Desktop/Code_Projects/lazyclaw/scripts/build-mobile-apk.sh 2>&1 | tail -8
```
Expected: `✓ Built ...app-release.apk` then `Published .../dist/app-release.apk (v0.1.0+1, sha256 ...)`. (If release signing complains, a debug build still installs — temporarily switch to `flutter build apk --debug` + copy `app-debug.apk`; real signing lands in the Hardening phase.)

- [ ] **Step 3: Verify the gateway serves it**

Run (with the server running, or via the test client):
```bash
cd ~/Desktop/Code_Projects/lazyclaw && python -c "
from fastapi import FastAPI; from fastapi.testclient import TestClient
from lazyclaw.gateway.routes.mobile import router
app=FastAPI(); app.include_router(router); c=TestClient(app)
print('version', c.get('/api/mobile/version').json())
print('apk bytes', len(c.get('/api/mobile/apk').content))
"
```
Expected: prints the version JSON and a non-zero byte count.

- [ ] **Step 4: Commit**

```bash
git add scripts/build-mobile-apk.sh
git commit -m "build(mobile): apk build+stamp script -> served dist"
```

---

## Task 16: Web Settings — "Mobile App" tab (download + QR + Android help)

**Files:**
- Create: `lazyclaw/web/src/pages/settings/MobileAppTab.tsx`
- Modify: `lazyclaw/web/src/pages/Settings.tsx` (add the tab)
- Modify: `lazyclaw/web/src/api.ts` (add `getMobileVersion`)

- [ ] **Step 1: API helper**

In `web/src/api.ts`, add:
```ts
export async function getMobileVersion(): Promise<
  { version: string; build: number; sha256: string; built_at: string } | null
> {
  const r = await fetch("/api/mobile/version", { credentials: "include" });
  if (!r.ok) return null;
  return r.json();
}
export const MOBILE_APK_URL = "/api/mobile/apk";
```

- [ ] **Step 2: The tab component (QR via a tiny inline generator dependency)**

Install the QR lib:
```bash
cd ~/Desktop/Code_Projects/lazyclaw/web && npm install qrcode.react@^4.2.0
```
Create `web/src/pages/settings/MobileAppTab.tsx`:
```tsx
import { useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { getMobileVersion, MOBILE_APK_URL } from "../../api";

export default function MobileAppTab() {
  const [v, setV] = useState<Awaited<ReturnType<typeof getMobileVersion>>>(null);
  const [absUrl, setAbsUrl] = useState("");
  useEffect(() => {
    getMobileVersion().then(setV);
    setAbsUrl(`${window.location.origin}${MOBILE_APK_URL}`);
  }, []);

  return (
    <div className="space-y-6 max-w-xl">
      <div>
        <h3 className="text-lg font-semibold">LazyClaw for Android</h3>
        {v ? (
          <p className="text-sm opacity-70">
            v{v.version} (build {v.build}) · built {new Date(v.built_at).toLocaleString()}
          </p>
        ) : (
          <p className="text-sm opacity-70">No build published yet.</p>
        )}
      </div>

      {v && (
        <div className="flex items-center gap-6">
          <a href={MOBILE_APK_URL} download
             className="px-4 py-2 rounded-lg bg-primary text-white font-medium">
            Download APK
          </a>
          <div className="bg-white p-3 rounded-lg">
            <QRCodeSVG value={absUrl} size={140} />
            <p className="text-xs text-black mt-1 text-center">Scan on your phone</p>
          </div>
        </div>
      )}

      <div className="rounded-lg border border-white/10 p-4 text-sm space-y-2">
        <p className="font-medium">Installing on Xiaomi (HyperOS):</p>
        <ol className="list-decimal ml-5 space-y-1 opacity-80">
          <li>Scan the QR with the phone (or open this page on the phone) and Download.</li>
          <li>When prompted, allow your browser to <b>Install unknown apps</b>.</li>
          <li>Open the app, enter your computer's address, and log in.</li>
          <li>For reliable background notifications later: Settings → Apps → LazyClaw →
              enable <b>Autostart</b> and set battery to <b>No restrictions</b>.</li>
        </ol>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Wire the tab into Settings**

In `web/src/pages/Settings.tsx`, find the tab list/array (the existing Models/Search/Teams/Permissions/About tabs) and add a `Mobile App` entry that renders `<MobileAppTab />` (import it at top). Mirror the existing tab structure exactly — do not invent a new tab mechanism.

- [ ] **Step 4: Verify the web build compiles**

Run: `cd ~/Desktop/Code_Projects/lazyclaw/web && npm run build 2>&1 | tail -6`
Expected: `✓ built` with no TypeScript errors.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/settings/MobileAppTab.tsx web/src/pages/Settings.tsx web/src/api.ts web/package.json web/package-lock.json
git commit -m "feat(web): mobile app settings tab (download + qr + install help)"
```

---

## Task 17: End-to-end smoke on a real LazyClaw server (desktop)

**Files:** none (verification).

- [ ] **Step 1: Start the gateway and the web app**

Run the server per the repo's normal dev flow (e.g. `make rebuild` / `lazyclaw start` and the web dev server). Confirm `/api/mobile/version` returns the stamped JSON and the Settings → Mobile App tab shows the Download button + QR.

- [ ] **Step 2: Install the APK on the Android emulator (sanity before the phone)**

Run:
```bash
cd ~/Desktop/Code_Projects/lazyclaw/mobile && flutter emulators --launch $(flutter emulators 2>/dev/null | awk 'NR==2{print $1}') 2>/dev/null || true
adb install -r build/app/outputs/flutter-apk/app-release.apk 2>&1 | tail -2
```
Expected: `Success`. (If no emulator exists, skip to Task 18 device install.)

- [ ] **Step 3: Verify login + chat against the running gateway**

In the app: open Server, set the gateway URL (use `10.0.2.2:18789` on the Android emulator to reach the host), log in with a real account, send a chat message, confirm a streamed reply renders. If the agent emits an `approval_request`, confirm the inline Approve/Deny buttons resolve it.

- [ ] **Step 4: Commit (record verification notes in the plan, nothing to code)**

Check this task's boxes once login + streamed chat is confirmed.

---

## Task 18: Install on the Xiaomi Mi 15 and confirm

**Files:** none (device verification).

- [ ] **Step 1: User action — enable sideloading**

On the Mi 15: Settings → enable **Install unknown apps** for the browser you'll download with. (One-time.)

- [ ] **Step 2: Download via the web Settings QR**

Open the LazyClaw web app on the phone (or scan the Settings → Mobile App QR), tap **Download APK**, and install when prompted.

- [ ] **Step 3: Configure + smoke test**

Open the app → Server → set the computer's LAN address (`<host-ip>:18789`) → log in → send a chat message → confirm the streamed reply. Confirm logout returns to the login screen.

- [ ] **Step 4: Record the result**

Check the boxes once the app logs in and chats from the Mi 15. **This completes Milestone A — a real, installable LazyClaw APK delivered from web Settings.**

---

## Self-Review

**Spec coverage (Milestone A slice of the spec):**
- Donor harvest (api_client copy-as-is + cookie repoint, theme, auth state machine, go_router guard) → Tasks 3, 7, 11, 13. ✓
- Drop client crypto → not ported (no CryptoService task). ✓
- WS chat contract (cookie `session_id`, no Origin, frame types, `message`/`approval_response`/`ping`) → Tasks 8–10, 12. ✓
- Self-hosted base URL config → Task 4 + Task 12 Step 1. ✓
- APK distribution (serve endpoint + version + web Settings download + QR + Android help) → Tasks 14–16. ✓
- Build-env wiring (JDK 17, ANDROID_HOME, donor sanity build) → Task 0. ✓
- HyperOS install/autostart guidance → Task 16 Step 2 + Task 18. ✓
- **Deferred (correctly out of this plan):** encrypted local DB, sync engine, tasks/budgets/notes CRUD, notifications, full chat cards/plan-gate, signing/release, iOS. Each gets its own plan.

**Placeholder scan:** No "TBD/handle errors/similar to Task N". Every code step contains real code; every run step has an expected result. The only intentionally manual tasks (17, 18) are device/server verification with concrete actions.

**Type/name consistency:** `ApiClient.getSessionCookie()`/`on401` (Tasks 3,7,12) match donor; `AuthTransport.postJson` (Tasks 6,7); `parseServerFrame`/`encodeClientMessage`/`encodeApprovalResponse` (Tasks 8,9,10,12); `ChatReducer.onUserSend/onFrame` + `ChatController.send/respondApproval` (Tasks 10,12); `baseUrlProvider`/`apiClientProvider`/`authProvider` (Tasks 7,12,13); backend `set_apk_dir`/`/api/mobile/version`/`/api/mobile/apk` (Tasks 14,15,16). Consistent. ✓

**Note for executor:** Commits in Tasks 14–16 touch the lazyclaw repo, which has unrelated in-flight changes on `feat/claude-agent-sdk` (cli.py, agent.py, SOUL.md). Stage **only** the files listed per task (`git add <explicit paths>`); the pre-commit hook may auto-stage adjacent files — if so, stash the unrelated paths by name first. Strongly prefer creating a dedicated branch (`feat/flutter-mobile`) before Task 1.
