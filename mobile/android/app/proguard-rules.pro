# ── flutter_local_notifications + Gson ───────────────────────────────────────
# flutter_local_notifications serialises scheduled notifications with Gson so
# they survive a reboot. Gson's TypeToken needs the generic SIGNATURE attribute
# preserved at runtime, or it throws
#   IllegalStateException: TypeToken must be created with a type argument
# the moment you call zonedSchedule — which breaks EVERY scheduled reminder.
# (These rules are only consulted when R8 shrinking is on; harmless otherwise.)
-keepattributes Signature
-keepattributes *Annotation*
-keepattributes InnerClasses
-keepattributes EnclosingMethod
-keep class com.dexterous.** { *; }
-keep class com.google.gson.** { *; }
-keep class com.google.gson.reflect.TypeToken { *; }
-keep class * extends com.google.gson.reflect.TypeToken
-keepclassmembers,allowobfuscation class * {
    @com.google.gson.annotations.SerializedName <fields>;
}

# ── Misc plugins that use reflection / generic signatures ────────────────────
-keep class net.sqlcipher.** { *; }
-dontwarn com.google.errorprone.annotations.**
