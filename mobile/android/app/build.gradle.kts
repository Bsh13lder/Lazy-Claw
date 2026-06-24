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
}

flutter {
    source = "../.."
}

dependencies {
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.4")
}
