import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/config/server_config.dart';
import '../providers/auth_provider.dart';
import '../providers/gateway_provider.dart';

class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});
  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final _user = TextEditingController();
  final _pass = TextEditingController();
  late final TextEditingController _server;
  String? _error;
  String? _serverNote;
  bool _busy = false;
  bool _showServer = false;

  @override
  void initState() {
    super.initState();
    // Seed the escape-hatch field with the URL auto-detect currently landed on,
    // so the user can SEE where the app is pointing and correct it if it's the
    // unreachable one.
    _server = TextEditingController(text: ref.read(baseUrlProvider));
  }

  @override
  void dispose() {
    _user.dispose();
    _pass.dispose();
    _server.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    final err = await ref
        .read(authProvider.notifier)
        .login(_user.text.trim(), _pass.text);
    if (mounted) {
      setState(() {
        _busy = false;
        _error = err;
      });
    }
  }

  /// Escape hatch: pin the app to a user-typed server URL and adopt it
  /// immediately, so a login can succeed even when auto-detect landed on an
  /// unreachable host (Dart can't resolve the `.local` mDNS name; the raw LAN
  /// IP works). Persisted via [GatewayController.setManual].
  Future<void> _useServer() async {
    final url = _server.text.trim();
    if (url.isEmpty) return;
    await ref.read(activeBaseUrlProvider.notifier).setManual(url);
    if (mounted) {
      setState(() {
        _serverNote =
            'Now using ${ServerConfig.normalizeBaseUrl(url)} — tap Log in.';
        _error = null;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('LazyClaw')),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(
                  controller: _user,
                  key: const Key('login_user'),
                  decoration: const InputDecoration(labelText: 'Username')),
              TextField(
                  controller: _pass,
                  key: const Key('login_pass'),
                  obscureText: true,
                  decoration: const InputDecoration(labelText: 'Password')),
              if (_error != null)
                Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Text(_error!,
                        style: const TextStyle(color: Colors.red))),
              const SizedBox(height: 20),
              FilledButton(
                key: const Key('login_submit'),
                onPressed: _busy ? null : _submit,
                child: _busy
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : const Text('Log in'),
              ),
              TextButton(
                onPressed: () => Navigator.pushNamed(context, '/register'),
                child: const Text('Create account'),
              ),
              const SizedBox(height: 24),
              // Server escape hatch — collapsed by default so it stays out of the
              // way, but always available so an unreachable auto-detect can never
              // hard-lock the login screen.
              TextButton(
                key: const Key('login_toggle_server'),
                onPressed: () => setState(() => _showServer = !_showServer),
                child: Text(_showServer
                    ? 'Hide server address'
                    : "Can't connect? Set server address"),
              ),
              if (_showServer) ...[
                TextField(
                  controller: _server,
                  key: const Key('login_server'),
                  autocorrect: false,
                  keyboardType: TextInputType.url,
                  decoration: const InputDecoration(
                    labelText: 'Server address',
                    hintText: 'http://192.168.0.12:18789',
                    helperText:
                        'On home WiFi use the Mac\'s LAN IP (mDNS .local names '
                        'often fail here).',
                    helperMaxLines: 2,
                  ),
                ),
                const SizedBox(height: 8),
                OutlinedButton(
                  key: const Key('login_use_server'),
                  onPressed: _useServer,
                  child: const Text('Save & use this server'),
                ),
                if (_serverNote != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text(_serverNote!,
                        style: const TextStyle(color: Colors.green)),
                  ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
