package com.lazyclaw.lazyclaw_mobile

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import org.json.JSONObject
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.android.RecognitionListener
import org.vosk.android.SpeechService
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.zip.ZipInputStream

/**
 * Always-on "Hey Lazy" wake word, fully on-device. A microphone foreground
 * service runs Vosk with a grammar limited to ["hey lazy", "[unk]"] so it only
 * ever matches the wake phrase (low CPU, few false positives). On a match it
 * posts a full-screen-intent notification that launches [MainActivity] over the
 * lock screen — the BAL-compliant way to surface UI from a background service.
 *
 * Detection runs entirely in Kotlin because the Flutter Vosk bindings conflict
 * with the app's on-device LLM (archive) and timezone (http) dependencies.
 */
class WakeWordService : Service(), RecognitionListener {
    companion object {
        const val SERVICE_CHANNEL = "hey_lazy_listening"
        const val WAKE_CHANNEL = "hey_lazy_wake"
        const val SERVICE_NOTIF_ID = 0x1A29
        const val WAKE_NOTIF_ID = 0x1A2A
        const val MODEL_NAME = "vosk-model-small-en-us-0.15"
        const val MODEL_URL =
            "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
        const val PHRASE = "hey lazy"
        const val GRAMMAR = "[\"hey lazy\", \"[unk]\"]"
        const val EXTRA_FROM_WAKE = "from_wake"
        const val DEBOUNCE_MS = 2500L

        // While the assistant holds the mic after a wake, Vosk detection pauses.
        const val MIC_HANDOFF_MS = 45000L

        @Volatile
        var isRunning = false
            private set

        fun modelDir(ctx: Context) = File(ctx.filesDir, MODEL_NAME)
        fun modelReady(ctx: Context) = File(modelDir(ctx), "am/final.mdl").exists()
    }

    // Written on the worker thread, read on the main thread (onDestroy / fireWake)
    // — @Volatile so a stop racing a start can't leak the mic + audio thread.
    @Volatile
    private var speechService: SpeechService? = null

    @Volatile
    private var model: Model? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var lastWakeMs = 0L
    private val mainHandler = Handler(Looper.getMainLooper())

    @Volatile
    private var stopping = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK, "LazyAssistant::WakeWord"
        ).apply {
            setReferenceCounted(false)
            acquire(12 * 60 * 60 * 1000L) // 12h cap (CPU only — does NOT light the screen)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // START_NOT_STICKY: a mic-typed FGS auto-restarted from the background on
        // Android 14+ can't re-enter the foreground and would crash-loop. The
        // app re-arms on next foreground instead (NativeWakeService.restoreAndRearm).
        if (speechService != null) return START_NOT_STICKY
        isRunning = true
        startForegroundCompat(buildServiceNotification("Starting…"))
        Thread {
            try {
                ensureModel()
                if (stopping) return@Thread
                startVosk()
                // A stop could have raced in while Vosk was initialising; if so,
                // tear the freshly-started recognizer down rather than leak it.
                if (stopping) {
                    try {
                        speechService?.stop()
                        speechService?.shutdown()
                    } catch (_: Exception) {
                    }
                    speechService = null
                    return@Thread
                }
                updateServiceNotification("Say \"Hey Lazy\"")
            } catch (e: Exception) {
                // Don't leave a zombie FGS holding the 12h wake-lock with a dead
                // recognizer — surface the error, then stop so isRunning is honest.
                updateServiceNotification("Wake word failed: ${e.message}")
                isRunning = false
                stopSelf()
            }
        }.start()
        return START_NOT_STICKY
    }

    private fun startForegroundCompat(n: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(SERVICE_NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(SERVICE_NOTIF_ID, n)
        }
    }

    /** Downloads + unzips the 40 MB model on first run (kept out of the APK). */
    private fun ensureModel() {
        if (modelReady(this)) return
        updateServiceNotification("Downloading voice model…")
        val zip = File(filesDir, "$MODEL_NAME.zip")
        (URL(MODEL_URL).openConnection() as HttpURLConnection).apply {
            connectTimeout = 30000
            readTimeout = 60000
            inputStream.use { input -> FileOutputStream(zip).use { input.copyTo(it) } }
            disconnect()
        }
        // The archive already contains a top-level "$MODEL_NAME/" folder.
        val baseCanonical = filesDir.canonicalPath + File.separator
        ZipInputStream(zip.inputStream().buffered()).use { zis ->
            var entry = zis.nextEntry
            while (entry != null) {
                val out = File(filesDir, entry.name)
                // Zip-Slip guard: a crafted entry name ("../…") must not write
                // outside filesDir. Refuse anything that escapes the base dir.
                if (!out.canonicalPath.startsWith(baseCanonical)) {
                    throw SecurityException("Unsafe zip entry: ${entry.name}")
                }
                if (entry.isDirectory) {
                    out.mkdirs()
                } else {
                    out.parentFile?.mkdirs()
                    FileOutputStream(out).use { zis.copyTo(it) }
                }
                entry = zis.nextEntry
            }
        }
        zip.delete()
    }

    private fun startVosk() {
        val m = Model(modelDir(this).absolutePath)
        model = m
        val recognizer = Recognizer(m, 16000.0f, GRAMMAR)
        val svc = SpeechService(recognizer, 16000.0f)
        speechService = svc
        svc.startListening(this)
    }

    private fun handleHypothesis(json: String?) {
        if (json == null) return
        val text = try {
            val o = JSONObject(json)
            val t = o.optString("text")
            if (t.isNotEmpty()) t else o.optString("partial")
        } catch (_: Exception) {
            ""
        }
        if (!text.lowercase().contains(PHRASE)) return
        val now = System.currentTimeMillis()
        if (now - lastWakeMs < DEBOUNCE_MS) return
        lastWakeMs = now
        fireWake()
    }

    override fun onPartialResult(hypothesis: String?) = handleHypothesis(hypothesis)
    override fun onResult(hypothesis: String?) = handleHypothesis(hypothesis)
    override fun onFinalResult(hypothesis: String?) = handleHypothesis(hypothesis)
    override fun onError(e: Exception?) {
        updateServiceNotification("Mic error: ${e?.message}")
    }

    override fun onTimeout() {}

    /**
     * Surfaces the assistant when "Hey Lazy" is heard. A full-screen-intent
     * notification is the only reliable way to launch an Activity from a
     * background service on Android 12–14; when the device is locked/screen-off
     * the system launches [MainActivity] over the lock screen. The launch carries
     * ACTION_ASSIST so the existing assist routing opens the assistant.
     */
    private fun fireWake() {
        ensureChannels()
        playEarcon() // audible "I heard you" — Google's mental model of a wake
        // Hand the mic to the assistant: Vosk's AudioRecord and the assistant's
        // recognizer can't both own the mic, so pause detection now and auto-
        // resume after the interaction window (the assistant captures in between).
        handoffMic()
        val launch = Intent(this, MainActivity::class.java).apply {
            action = Intent.ACTION_ASSIST
            putExtra(EXTRA_FROM_WAKE, true)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        }
        val pi = PendingIntent.getActivity(
            this, 1, launch,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val n = NotificationCompat.Builder(this, WAKE_CHANNEL)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("Hey Lazy")
            .setContentText("Tap to talk to Lazy")
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setFullScreenIntent(pi, true)
            .setAutoCancel(true)
            .build()
        (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
            .notify(WAKE_NOTIF_ID, n)
    }

    /** Short notification beep confirming the wake phrase was heard. */
    private fun playEarcon() {
        try {
            val tg = ToneGenerator(AudioManager.STREAM_NOTIFICATION, 80)
            tg.startTone(ToneGenerator.TONE_PROP_BEEP, 150)
            mainHandler.postDelayed({ try { tg.release() } catch (_: Exception) {} }, 350)
        } catch (_: Exception) {/* earcon is best-effort */}
    }

    /** Pause Vosk to release the mic for the assistant; resume detection later. */
    private fun handoffMic() {
        try {
            speechService?.setPause(true)
        } catch (_: Exception) {
        }
        mainHandler.postDelayed({
            try {
                if (!stopping) speechService?.setPause(false)
            } catch (_: Exception) {
            }
        }, MIC_HANDOFF_MS)
    }

    private fun ensureChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(
            NotificationChannel(
                SERVICE_CHANNEL, "Hey Lazy listening",
                NotificationManager.IMPORTANCE_LOW
            )
        )
        nm.createNotificationChannel(
            NotificationChannel(
                WAKE_CHANNEL, "Hey Lazy wake",
                NotificationManager.IMPORTANCE_HIGH
            )
        )
    }

    private fun buildServiceNotification(text: String): Notification {
        ensureChannels()
        val open = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, SERVICE_CHANNEL)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("Hey Lazy is listening")
            .setContentText(text)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true)
            .setContentIntent(open)
            .build()
    }

    private fun updateServiceNotification(text: String) {
        (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
            .notify(SERVICE_NOTIF_ID, buildServiceNotification(text))
    }

    override fun onDestroy() {
        stopping = true
        isRunning = false
        mainHandler.removeCallbacksAndMessages(null) // drop a pending mic-resume
        try {
            speechService?.stop()
            speechService?.shutdown()
        } catch (_: Exception) {
        }
        speechService = null
        try {
            model?.close()
        } catch (_: Exception) {
        }
        model = null
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
        super.onDestroy()
    }
}
