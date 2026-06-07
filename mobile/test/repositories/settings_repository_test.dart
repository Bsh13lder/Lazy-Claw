import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/settings_repository.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

class _FakeTransport implements SettingsTransport {
  String? lastMethod;
  String? lastPath;
  Map<String, dynamic>? lastBody;

  Map<String, dynamic> response;

  _FakeTransport(this.response);

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    lastMethod = 'GET';
    lastPath = path;
    return response;
  }

  @override
  Future<Map<String, dynamic>> postJson(
      String path, Map<String, dynamic> body) async {
    lastMethod = 'POST';
    lastPath = path;
    lastBody = body;
    return response;
  }

  @override
  Future<Map<String, dynamic>> patchJson(
      String path, Map<String, dynamic> body) async {
    lastMethod = 'PATCH';
    lastPath = path;
    lastBody = body;
    return response;
  }
}

// ── Fixtures ───────────────────────────────────────────────────────────────

Map<String, dynamic> _ecoEnvelope({
  String mode = 'HYBRID',
  List<String> modes = const ['HYBRID', 'FULL', 'CLAUDE', 'MINIMAX'],
  String brain = 'claude-sonnet-4-6',
  String worker = 'gemma4:e2b',
  String fallback = 'claude-haiku-4-5',
}) =>
    {
      'success': true,
      'data': {
        'mode': mode,
        'modes': modes,
        'brain': brain,
        'worker': worker,
        'fallback': fallback,
      },
    };

Map<String, dynamic> _permEnvelope({
  Map<String, String> categoryDefaults = const {
    'core': 'allow',
    'vault': 'ask',
    'browser_management': 'allow',
  },
  Map<String, String> skillOverrides = const {},
  int autoApproveTimeout = 300,
  bool requireApprovalForHeartbeat = true,
}) =>
    {
      'success': true,
      'data': {
        'category_defaults': categoryDefaults,
        'skill_overrides': skillOverrides,
        'auto_approve_timeout': autoApproveTimeout,
        'require_approval_for_heartbeat': requireApprovalForHeartbeat,
      },
    };

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  // ── getEco ────────────────────────────────────────────────────────────────
  group('SettingsRepository.getEco', () {
    test('GETs /api/settings/eco and parses EcoSettings', () async {
      final t = _FakeTransport(_ecoEnvelope());
      final repo = SettingsRepository(t);
      final eco = await repo.getEco();
      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/settings/eco');
      expect(eco.mode, 'HYBRID');
      expect(eco.modes, ['HYBRID', 'FULL', 'CLAUDE', 'MINIMAX']);
      expect(eco.brain, 'claude-sonnet-4-6');
      expect(eco.worker, 'gemma4:e2b');
      expect(eco.fallback, 'claude-haiku-4-5');
    });

    test('throws SettingsException on success:false', () async {
      final t = _FakeTransport(
          {'success': false, 'error': 'backend down'});
      final repo = SettingsRepository(t);
      expect(
        () => repo.getEco(),
        throwsA(isA<SettingsException>().having(
          (e) => e.message,
          'message',
          'backend down',
        )),
      );
    });

    test('uses generic error message when error key absent', () async {
      final t = _FakeTransport({'success': false});
      expect(
        () => SettingsRepository(t).getEco(),
        throwsA(isA<SettingsException>().having(
          (e) => e.message,
          'message',
          'Unknown server error',
        )),
      );
    });
  });

  // ── setEco ────────────────────────────────────────────────────────────────
  group('SettingsRepository.setEco', () {
    test('POSTs mode to /api/settings/eco and returns updated EcoSettings',
        () async {
      final t = _FakeTransport(_ecoEnvelope(mode: 'FULL'));
      final repo = SettingsRepository(t);
      final eco = await repo.setEco('FULL');
      expect(t.lastMethod, 'POST');
      expect(t.lastPath, '/api/settings/eco');
      expect(t.lastBody, {'mode': 'FULL'});
      expect(eco.mode, 'FULL');
    });

    test('throws SettingsException on invalid mode', () async {
      final t = _FakeTransport({
        'success': false,
        'error': 'Invalid mode: BOGUS',
      });
      expect(
        () => SettingsRepository(t).setEco('BOGUS'),
        throwsA(isA<SettingsException>().having(
          (e) => e.message,
          'message',
          'Invalid mode: BOGUS',
        )),
      );
    });

    test('reflects returned mode (not just echoes the sent mode)', () async {
      // Server may normalise the mode string — repo must use server's response.
      final t = _FakeTransport(_ecoEnvelope(mode: 'MINIMAX'));
      final eco = await SettingsRepository(t).setEco('minimax');
      expect(eco.mode, 'MINIMAX');
    });
  });

  // ── getGeneral (operating mode) ───────────────────────────────────────────
  group('SettingsRepository.getGeneral', () {
    test('GETs /api/settings/general and parses agent_mode', () async {
      final t = _FakeTransport({
        'success': true,
        'data': {'agent_mode': 'plan'},
      });
      final general = await SettingsRepository(t).getGeneral();
      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/settings/general');
      expect(general.agentMode, 'plan');
    });

    test('defaults to "ask" when agent_mode is missing', () async {
      final t = _FakeTransport({'success': true, 'data': {}});
      final general = await SettingsRepository(t).getGeneral();
      expect(general.agentMode, 'ask');
    });

    test('coerces an unknown agent_mode to the default', () async {
      final t = _FakeTransport({
        'success': true,
        'data': {'agent_mode': 'bogus'},
      });
      final general = await SettingsRepository(t).getGeneral();
      expect(general.agentMode, 'ask');
    });

    test('throws SettingsException on success:false', () async {
      final t = _FakeTransport({'success': false, 'error': 'nope'});
      expect(
        () => SettingsRepository(t).getGeneral(),
        throwsA(isA<SettingsException>()),
      );
    });
  });

  // ── setAgentMode (operating mode) ─────────────────────────────────────────
  group('SettingsRepository.setAgentMode', () {
    test('PATCHes {agent_mode} to /api/settings/general', () async {
      final t = _FakeTransport({
        'success': true,
        'data': {'agent_mode': 'auto'},
      });
      final general = await SettingsRepository(t).setAgentMode('auto');
      expect(t.lastMethod, 'PATCH');
      expect(t.lastPath, '/api/settings/general');
      expect(t.lastBody, {'agent_mode': 'auto'});
      expect(general.agentMode, 'auto');
    });

    test('falls back to the requested mode when server omits agent_mode',
        () async {
      // Backend not yet emitting agent_mode → still reflect the user's choice.
      final t = _FakeTransport({'success': true, 'data': {}});
      final general = await SettingsRepository(t).setAgentMode('plan');
      expect(general.agentMode, 'plan');
    });

    test('honors the server-echoed mode over the requested one', () async {
      final t = _FakeTransport({
        'success': true,
        'data': {'agent_mode': 'ask'},
      });
      final general = await SettingsRepository(t).setAgentMode('chat');
      expect(general.agentMode, 'ask');
    });

    test('throws SettingsException on success:false', () async {
      final t = _FakeTransport({'success': false, 'error': 'No fields'});
      expect(
        () => SettingsRepository(t).setAgentMode('chat'),
        throwsA(isA<SettingsException>().having(
          (e) => e.message,
          'message',
          'No fields',
        )),
      );
    });
  });

  // ── GeneralSettings model ─────────────────────────────────────────────────
  group('GeneralSettings', () {
    test('coerceAgentMode normalizes case and whitespace', () {
      expect(GeneralSettings.coerceAgentMode('  PLAN '), 'plan');
      expect(GeneralSettings.coerceAgentMode('Auto'), 'auto');
    });

    test('coerceAgentMode falls back to default on null / unknown', () {
      expect(GeneralSettings.coerceAgentMode(null), 'ask');
      expect(GeneralSettings.coerceAgentMode('xyz'), 'ask');
    });

    test('exposes the four contract modes in order', () {
      expect(kAgentModeLabels.keys.toList(),
          ['chat', 'ask', 'plan', 'auto']);
      expect(kAgentModeLabels['auto'], 'Execute');
    });
  });

  // ── getPermissions ────────────────────────────────────────────────────────
  group('SettingsRepository.getPermissions', () {
    test('GETs /api/permissions/settings and parses PermissionsSettings',
        () async {
      final t = _FakeTransport(_permEnvelope(
        categoryDefaults: {'core': 'allow', 'vault': 'ask'},
      ));
      final repo = SettingsRepository(t);
      final perms = await repo.getPermissions();
      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/permissions/settings');
      expect(perms.categoryDefaults['core'], 'allow');
      expect(perms.categoryDefaults['vault'], 'ask');
      expect(perms.autoApproveTimeout, 300);
      expect(perms.requireApprovalForHeartbeat, isTrue);
    });

    test('returns empty categoryDefaults when key is missing', () async {
      final t = _FakeTransport({
        'success': true,
        'data': {
          'skill_overrides': {},
          'auto_approve_timeout': 300,
          'require_approval_for_heartbeat': false,
        },
      });
      final perms = await SettingsRepository(t).getPermissions();
      expect(perms.categoryDefaults, isEmpty);
    });

    test('throws SettingsException on success:false', () async {
      final t = _FakeTransport({'success': false, 'error': 'forbidden'});
      expect(
        () => SettingsRepository(t).getPermissions(),
        throwsA(isA<SettingsException>()),
      );
    });
  });

  // ── setCategoryPermission ─────────────────────────────────────────────────
  group('SettingsRepository.setCategoryPermission', () {
    test('PATCHes the correct shape to /api/permissions/settings', () async {
      final t = _FakeTransport(_permEnvelope(
        categoryDefaults: {'core': 'allow', 'vault': 'allow'},
      ));
      final repo = SettingsRepository(t);
      final perms = await repo.setCategoryPermission('vault', 'allow');
      expect(t.lastMethod, 'PATCH');
      expect(t.lastPath, '/api/permissions/settings');
      expect(t.lastBody, {
        'category_defaults': {'vault': 'allow'},
      });
      expect(perms.categoryDefaults['vault'], 'allow');
    });

    test('throws SettingsException when server returns success:false', () async {
      final t = _FakeTransport(
          {'success': false, 'error': 'Category not found'});
      expect(
        () => SettingsRepository(t).setCategoryPermission('core', 'deny'),
        throwsA(isA<SettingsException>().having(
          (e) => e.message,
          'message',
          'Category not found',
        )),
      );
    });
  });

  // ── EcoSettings model ─────────────────────────────────────────────────────
  group('EcoSettings', () {
    test('copyWith changes only mode', () {
      const eco = EcoSettings(
        mode: 'HYBRID',
        modes: ['HYBRID', 'FULL'],
        brain: 'brain',
        worker: 'worker',
        fallback: 'fallback',
      );
      final updated = eco.copyWith(mode: 'FULL');
      expect(updated.mode, 'FULL');
      expect(updated.modes, ['HYBRID', 'FULL']);
      expect(updated.brain, 'brain');
    });
  });

  // ── PermissionsSettings model ─────────────────────────────────────────────
  group('PermissionsSettings', () {
    test('withCategory returns new instance with updated level', () {
      const perms = PermissionsSettings(
        categoryDefaults: {'core': 'allow', 'vault': 'ask'},
        skillOverrides: {},
        autoApproveTimeout: 300,
        requireApprovalForHeartbeat: true,
      );
      final updated = perms.withCategory('vault', 'allow');
      expect(updated.categoryDefaults['vault'], 'allow');
      expect(updated.categoryDefaults['core'], 'allow');
      // original is unchanged (immutable)
      expect(perms.categoryDefaults['vault'], 'ask');
    });

    test('withCategory adds a new category', () {
      const perms = PermissionsSettings(
        categoryDefaults: {},
        skillOverrides: {},
        autoApproveTimeout: 300,
        requireApprovalForHeartbeat: false,
      );
      final updated = perms.withCategory('tasks', 'deny');
      expect(updated.categoryDefaults['tasks'], 'deny');
    });
  });
}
