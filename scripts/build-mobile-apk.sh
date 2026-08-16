#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MOBILE="$ROOT/mobile"
DIST="$MOBILE/dist"
mkdir -p "$DIST"

cd "$MOBILE"

# Publishing gate: a release APK MUST be signed with the real keystore.
# Gradle silently falls back to DEBUG signing when android/key.properties is
# absent (deliberate, so keystore-less machines can still build) — but a
# debug-signed build published to dist/ makes every phone's updater fail with
# "package conflicts with an existing package" (2026-07-31: a worktree build
# had no gitignored key.properties and shipped debug-signed). Fail HERE, before
# the 2-minute build, with the fix spelled out. Set ALLOW_DEBUG_SIGNED=1 only
# for a local install you will adb-push to a device with no prior install.
if [ ! -f "$MOBILE/android/key.properties" ] && [ "${ALLOW_DEBUG_SIGNED:-0}" != "1" ]; then
    echo "ERROR: $MOBILE/android/key.properties not found — this build would be" >&2
    echo "DEBUG-signed and every phone would reject it as a package conflict." >&2
    echo "Copy key.properties + the .jks it names from the main checkout" >&2
    echo "(mobile/android/ + mobile/android/app/, both gitignored) or from" >&2
    echo "~/.lazyclaw/keystore-backup, then re-run." >&2
    exit 2
fi

flutter build apk --release

SRC="$MOBILE/build/app/outputs/flutter-apk/app-release.apk"
cp "$SRC" "$DIST/app-release.apk"

VER="$(grep '^version:' "$MOBILE/pubspec.yaml" | awk '{print $2}')"
NAME="${VER%%+*}"; BUILD="${VER##*+}"
SHA="$(shasum -a 256 "$DIST/app-release.apk" | awk '{print $1}')"
BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > "$DIST/version.json" <<EOF
{"version":"$NAME","build":$BUILD,"sha256":"$SHA","built_at":"$BUILT_AT","min_android":"7.0"}
EOF
echo "Published $DIST/app-release.apk (v$NAME+$BUILD, sha256 $SHA)"
