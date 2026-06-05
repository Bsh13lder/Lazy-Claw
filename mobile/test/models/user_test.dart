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
