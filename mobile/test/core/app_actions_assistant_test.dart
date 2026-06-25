import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/actions/app_actions.dart';

void main() {
  test('assist shortcut + uri resolve to AppAction.assistant', () {
    expect(appActionForShortcut('assist'), AppAction.assistant);
    expect(appActionForUri(Uri.parse('lazyclaw://assistant')),
        AppAction.assistant);
    expect(routeForAction(AppAction.assistant), '/assistant');
  });
}
