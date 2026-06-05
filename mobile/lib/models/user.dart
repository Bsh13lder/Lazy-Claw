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
