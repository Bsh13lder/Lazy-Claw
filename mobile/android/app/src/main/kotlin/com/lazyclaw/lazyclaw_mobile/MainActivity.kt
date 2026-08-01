package com.lazyclaw.lazyclaw_mobile

import android.Manifest
import android.app.KeyguardManager
import android.app.NotificationManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.core.content.ContextCompat
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val channel = "com.lazyclaw/system_settings"
    private val assistChannel = "lazy/assist"
    private val wakeChannel = "lazyclaw/wake"

    // The Dart side handler for the ASSIST gesture. Resolved once the engine is
    // configured. ACTION_ASSIST can arrive on the LAUNCHING intent before the
    // engine exists (cold start), so we queue it and flush once the channel is up.
    private var assist: MethodChannel? = null
    private var pendingAssist = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Show the assistant over the lock screen + turn the screen on ONLY when
        // the wake word / assist gesture launched us (API 27+). Gating on the
        // launch intent stops a normal launcher open from dismissing the keyguard.
        // The mic foreground service holds a PARTIAL_WAKE_LOCK for CPU; these
        // flags handle the screen itself.
        if (isAssist(intent) && Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
            (getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager)
                .requestDismissKeyguard(this, null)
        }
    }

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

        // "Hey Lazy" wake-word foreground service control.
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, wakeChannel)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "start" -> {
                        // Vosk's AudioRecord needs RECORD_AUDIO. If it isn't
                        // granted the service would start but silently never
                        // hear anything, so report failure and let the toggle
                        // reflect reality instead of lying "On".
                        val micGranted = ContextCompat.checkSelfPermission(
                            this, Manifest.permission.RECORD_AUDIO,
                        ) == PackageManager.PERMISSION_GRANTED
                        if (!micGranted) {
                            result.success(false)
                        } else {
                            try {
                                val svc = Intent(this, WakeWordService::class.java)
                                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                                    startForegroundService(svc)
                                } else {
                                    startService(svc)
                                }
                                result.success(true)
                            } catch (e: Exception) {
                                result.error("WAKE_START_FAILED", e.message, null)
                            }
                        }
                    }
                    "stop" -> {
                        stopService(Intent(this, WakeWordService::class.java))
                        result.success(true)
                    }
                    "isRunning" -> result.success(WakeWordService.isRunning)
                    "modelReady" -> result.success(WakeWordService.modelReady(this))
                    // Requests RECORD_AUDIO natively.
                    //
                    // The Dart side used to trigger this by calling
                    // SpeechToText().initialize() purely for its permission
                    // prompt — but that plugin is a process singleton whose
                    // initialize() early-returns without replacing callbacks, so
                    // whoever calls it FIRST owns onError/onStatus forever. The
                    // wake service ran first at boot, which silently left the
                    // assistant's error handler unwired and any recognizer error
                    // stuck on "Listening..." with a dead mic.
                    //
                    // Fire-and-forget: the grant arrives asynchronously and the
                    // caller re-checks micGranted, so there is no result to wait
                    // for here.
                    "requestMic" -> {
                        try {
                            androidx.core.app.ActivityCompat.requestPermissions(
                                this, arrayOf(Manifest.permission.RECORD_AUDIO), 4201
                            )
                            result.success(true)
                        } catch (e: Exception) {
                            result.success(false)
                        }
                    }
                    "micGranted" -> result.success(
                        ContextCompat.checkSelfPermission(
                            this, Manifest.permission.RECORD_AUDIO,
                        ) == PackageManager.PERMISSION_GRANTED
                    )
                    // Android 14+ revokes USE_FULL_SCREEN_INTENT by default for
                    // non-call apps; without it a wake posts only a silent
                    // notification instead of surfacing over the lock screen.
                    "canFullScreenIntent" -> {
                        val ok = if (Build.VERSION.SDK_INT >= 34) {
                            (getSystemService(Context.NOTIFICATION_SERVICE)
                                as NotificationManager).canUseFullScreenIntent()
                        } else {
                            true
                        }
                        result.success(ok)
                    }
                    "requestFullScreenIntent" -> {
                        val ok = try {
                            if (Build.VERSION.SDK_INT >= 34) {
                                startActivity(
                                    Intent(
                                        Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT,
                                        Uri.fromParts("package", packageName, null),
                                    ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                                )
                            }
                            true
                        } catch (_: Exception) {
                            false
                        }
                        result.success(ok)
                    }
                    // Build.MANUFACTURER drives Xiaomi/HyperOS detection — the old
                    // "miui" version-string sniff misses HyperOS 2 (e.g. Mi 15).
                    "deviceManufacturer" -> result.success(Build.MANUFACTURER ?: "")
                    // Launch an OEM settings page (MIUI autostart/battery/popup),
                    // falling back to the app-details page if the component is
                    // missing on this MIUI/HyperOS build.
                    "launchIntent" -> {
                        val ok = launchSettingIntent(
                            call.argument("action"),
                            call.argument("package"),
                            call.argument("component"),
                            call.argument("extras"),
                        )
                        result.success(ok)
                    }
                    else -> result.notImplemented()
                }
            }

        // Dedicated channel for the system ASSIST gesture → opens "Hey Lazy".
        // The wake-word service launches MainActivity with ACTION_ASSIST too, so
        // this same path surfaces the assistant on a wake.
        assist = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, assistChannel)
        if (isAssist(intent) || pendingAssist) {
            pendingAssist = false
            assist?.invokeMethod("assist", null)
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        // Warm path: ASSIST gesture or a wake-word launch while already running.
        if (isAssist(intent)) {
            val ch = assist
            if (ch != null) ch.invokeMethod("assist", null) else pendingAssist = true
        }
    }

    private fun isAssist(intent: Intent?): Boolean =
        intent?.action == Intent.ACTION_ASSIST ||
            intent?.getBooleanExtra(WakeWordService.EXTRA_FROM_WAKE, false) == true

    @Suppress("UNCHECKED_CAST")
    private fun launchSettingIntent(
        action: String?,
        pkg: String?,
        component: String?,
        extras: Map<String, String>?,
    ): Boolean {
        return try {
            val intent = if (action != null) Intent(action) else Intent()
            if (pkg != null && component != null) {
                intent.component = ComponentName(pkg, component)
            }
            extras?.forEach { (k, v) -> intent.putExtra(k, v) }
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            startActivity(intent)
            true
        } catch (_: Exception) {
            try {
                startActivity(
                    Intent(
                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        Uri.fromParts("package", packageName, null)
                    ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
                true
            } catch (_: Exception) {
                false
            }
        }
    }
}
