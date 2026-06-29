import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/wake/miui_permissions.dart';

void main() {
  test('emits the three MIUI targets with correct components', () {
    final t = miuiTargets('com.lazyclaw.lazyclaw_mobile');
    expect(t.map((e) => e.key), ['autostart', 'battery', 'background_popup']);

    final autostart = t.firstWhere((e) => e.key == 'autostart');
    expect(autostart.package, 'com.miui.securitycenter');
    expect(autostart.component,
        'com.miui.permcenter.autostart.AutoStartManagementActivity');

    final popup = t.firstWhere((e) => e.key == 'background_popup');
    expect(popup.action, 'miui.intent.action.APP_PERM_EDITOR');
    expect(popup.component,
        'com.miui.permcenter.permissions.PermissionsEditorActivity');
    expect(popup.extras['extra_pkgname'], 'com.lazyclaw.lazyclaw_mobile');
  });
}
