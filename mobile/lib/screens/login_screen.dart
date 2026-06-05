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
