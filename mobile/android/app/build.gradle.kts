import java.util.Properties
import java.io.FileInputStream

plugins {
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// Release signing — loaded from android/key.properties (gitignored). Falls back
// to debug signing when key.properties is absent so the build still works on a
// machine without the keystore. The SAME key must sign every release for the
// in-app self-updater to install over an existing install.
val keystoreProperties = Properties()
val keystorePropertiesFile = rootProject.file("key.properties")
if (keystorePropertiesFile.exists()) {
    keystoreProperties.load(FileInputStream(keystorePropertiesFile))
}

android {
    namespace = "com.lazyclaw.lazyclaw_mobile"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        // Required by flutter_local_notifications (uses java.time backport).
        isCoreLibraryDesugaringEnabled = true
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_17.toString()
    }

    defaultConfig {
        applicationId = "com.lazyclaw.lazyclaw_mobile"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName

        // The phone is arm64. Native-assets (llamadart's llama.cpp .so) get built
        // for every host architecture and `--target-platform` does NOT filter
        // them, so without this the APK also ships ~110 MB of useless x86_64
        // libs. Restrict packaging to arm64-v8a.
        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    signingConfigs {
        create("release") {
            if (keystorePropertiesFile.exists()) {
                keyAlias = keystoreProperties["keyAlias"] as String
                keyPassword = keystoreProperties["keyPassword"] as String
                storeFile = file(keystoreProperties["storeFile"] as String)
                storePassword = keystoreProperties["storePassword"] as String
            }
        }
    }

    buildTypes {
        release {
            // Use the real release key when key.properties is present (durable
            // in-app self-update); otherwise fall back to debug so the build runs.
            signingConfig = if (keystorePropertiesFile.exists())
                signingConfigs.getByName("release")
            else
                signingConfigs.getByName("debug")

            // Flutter 3.41's `flutter build apk --release` enables R8 shrinking by
            // default, which strips the generic type signatures Gson relies on →
            // flutter_local_notifications' scheduled-notification store throws
            // "TypeToken must be created with a type argument" on EVERY
            // zonedSchedule, breaking all scheduled reminders (and crashing the
            // startup reschedule). Disable shrinking for this self-hosted app —
            // reliability over a few MB. proguard-rules.pro is still wired so the
            // keep rules apply if shrinking is ever turned back on.
            isMinifyEnabled = false
            isShrinkResources = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    // Drop every non-arm64 native library at PACKAGING time.
    //
    // `defaultConfig.ndk.abiFilters` above says arm64-v8a only, and it does not
    // work: it governs libraries AGP itself builds/merges, but Flutter's
    // native-assets pipeline (llamadart's llama.cpp/ggml, plus the Vosk AAR)
    // contributes prebuilt .so through a path that filter never sees. The
    // shipped 1.22.6 APK proved it — 205 MB carrying THREE ABIs:
    //
    //     x86_64        99.1 MB   ← emulator-only, cannot run on any phone
    //     arm64-v8a     55.3 MB   ← the only one this app targets
    //     armeabi-v7a   33.6 MB   ← 32-bit, below our minSdk reality
    //
    // Worst single offender: lib/x86_64/libggml-vulkan.so at 48.6 MB. The
    // `llamadart_native_backends` block in pubspec.yaml pins CPU-only, but it
    // is keyed per-platform and only lists `android-arm64` — so x86_64 fell
    // through to the default (all backends) and dragged Vulkan in.
    //
    // Excluding at packaging is the robust lever: it applies to the final APK
    // assembly no matter which upstream produced the .so, so it cannot be
    // bypassed the way abiFilters was.
    packaging {
        jniLibs {
            excludes += setOf("lib/x86_64/**", "lib/armeabi-v7a/**", "lib/x86/**")
        }
    }
}

flutter {
    source = "../.."
}

dependencies {
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.4")
    // Native offline wake-word ("Hey Lazy"). The Flutter Vosk bindings conflict
    // with llamadart (archive) and timezone (http), so we use the native Android
    // Vosk library directly from a Kotlin foreground service instead.
    implementation("com.alphacephei:vosk-android:0.3.47")
}
