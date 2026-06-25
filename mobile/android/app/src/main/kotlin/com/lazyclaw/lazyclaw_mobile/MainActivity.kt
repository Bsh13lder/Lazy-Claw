package com.lazyclaw.lazyclaw_mobile

import android.content.Intent
import android.provider.Settings
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val channel = "com.lazyclaw/system_settings"
    private val assistChannel = "lazy/assist"

    // The Dart side handler for the ASSIST gesture. Resolved once the engine is
    // configured. ACTION_ASSIST can arrive on the LAUNCHING intent before the
    // engine exists (cold start), so we queue it and flush once the channel is up.
    private var assist: MethodChannel? = null
    private var pendingAssist = false

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channel)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    // Open the system keyboard / input-method picker so the user
                    // can enable the AI Keyboard after installing it.
                    "openInputMethodSettings" -> {
                        try {
                            val intent = Intent(Settings.ACTION_INPUT_METHOD_SETTINGS)
                            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                            startActivity(intent)
                            result.success(true)
                        } catch (e: Exception) {
                            result.error("UNAVAILABLE", e.message, null)
                        }
                    }
                    else -> result.notImplemented()
                }
            }

        // Dedicated channel for the system ASSIST gesture → opens "Hey Lazy".
        assist = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, assistChannel)
        // The launching intent may already be an ASSIST (cold start).
        if (isAssist(intent) || pendingAssist) {
            pendingAssist = false
            assist?.invokeMethod("assist", null)
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        // Warm path: the app is already running and the user fires the ASSIST
        // gesture again. Forward immediately, or queue if the engine isn't ready.
        if (isAssist(intent)) {
            val ch = assist
            if (ch != null) ch.invokeMethod("assist", null) else pendingAssist = true
        }
    }

    private fun isAssist(intent: Intent?): Boolean =
        intent?.action == Intent.ACTION_ASSIST
}
