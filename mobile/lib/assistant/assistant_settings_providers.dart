/// Local mirrors of the "Hey Lazy" privacy flags.
///
/// Seeded from the server's general settings on app start (see the settings
/// screen), and written through on toggle. Default OFF / on-device-first
/// (CLAUDE.md): process-on-device default false, confirm-cloud default true.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

final assistantOnDeviceOnlyProvider = StateProvider<bool>((_) => false);
final assistantConfirmCloudProvider = StateProvider<bool>((_) => true);
final assistantFirstCloudConsentGivenProvider = StateProvider<bool>((_) => false);
