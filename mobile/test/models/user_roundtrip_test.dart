import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/user.dart';

void main() {
  test('User.fromJson(toJson()) round-trips all fields', () {
    const original = User(
      id: 'uuid-1',
      username: 'sam',
      displayName: 'Sam',
      role: 'admin',
    );
    final restored = User.fromJson(original.toJson());
    expect(restored.id, original.id);
    expect(restored.username, original.username);
    expect(restored.displayName, original.displayName);
    expect(restored.role, original.role);
  });

  test('round-trip preserves null displayName', () {
    const original = User(id: 'x', username: 'y', role: 'user');
    final restored = User.fromJson(original.toJson());
    expect(restored.displayName, isNull);
    expect(restored.id, 'x');
    expect(restored.username, 'y');
    expect(restored.role, 'user');
  });

  test('toJson uses the API field names fromJson reads', () {
    const u = User(id: 'a', username: 'b', displayName: 'c', role: 'd');
    final json = u.toJson();
    expect(json.keys,
        containsAll(<String>['id', 'username', 'display_name', 'role']));
    expect(json['display_name'], 'c');
  });
}
