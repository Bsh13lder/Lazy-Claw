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
        // Defensive coercion: never hard-cast external API data.
        id: (json['id'] ?? '').toString(),
        username: (json['username'] ?? '').toString(),
        displayName: json['display_name'] as String?,
        role: (json['role'] as String?) ?? 'user',
      );
}
