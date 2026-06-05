import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/config/server_config.dart';
import '../providers/auth_provider.dart';

class ServerSetupScreen extends ConsumerStatefulWidget {
  const ServerSetupScreen({super.key});
  @override
  ConsumerState<ServerSetupScreen> createState() => _ServerSetupScreenState();
}

class _ServerSetupScreenState extends ConsumerState<ServerSetupScreen> {
  final _ctrl = TextEditingController();
  @override
  void initState() {
    super.initState();
    ServerConfig.load().then((v) => _ctrl.text = v);
  }
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Connect to your LazyClaw')),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(children: [
          const Text('Enter your computer\'s address (e.g. 192.168.1.5:18789)'),
          const SizedBox(height: 12),
          TextField(controller: _ctrl,
              decoration: const InputDecoration(labelText: 'Gateway URL')),
          const SizedBox(height: 20),
          FilledButton(
            onPressed: () async {
              final url = ServerConfig.normalizeBaseUrl(_ctrl.text);
              await ServerConfig.save(url);
              ref.read(baseUrlProvider.notifier).state = url;
              if (context.mounted) Navigator.pop(context);
            },
            child: const Text('Save'),
          ),
        ]),
      ),
    );
  }
}
