#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MOBILE="$ROOT/mobile"
DIST="$MOBILE/dist"
mkdir -p "$DIST"

cd "$MOBILE"
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
