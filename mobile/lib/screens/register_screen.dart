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
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2))
                  : const Text('Register')),
        ]),
      ),
    );
  }
}
