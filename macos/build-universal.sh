#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/build/core-mac"
APP="${APP:-$BUILD/Core-Mac.app}"

mkdir -p "$BUILD"

echo "============================================================"
echo " Core-Mac Universal 2 build"
echo " arm64 + x86_64"
echo "============================================================"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: Core-Mac Universal build must run on macOS."
  exit 1
fi

for tool in xcodebuild lipo file; do
  command -v "$tool" >/dev/null || {
    echo "ERROR: required Apple build tool missing: $tool"
    exit 1
  }
done

cat <<'EOF'
Core-Mac build policy:
  - ARCHS="arm64 x86_64"
  - ONLY_ACTIVE_ARCH=NO
  - macOS 13+
  - no Rosetta-only payloads
  - no external Homebrew/Python runtime dependency
EOF

# The Swift/Xcode Core-Mac app target is added during the native shell merge.
# Once present, build both slices into a single archive/product.
if [[ -d "$ROOT/CoreMac.xcodeproj" || -d "$ROOT/CoreMac.xcworkspace" ]]; then
  PROJECT_ARGS=()
  if [[ -d "$ROOT/CoreMac.xcworkspace" ]]; then
    PROJECT_ARGS=(-workspace "$ROOT/CoreMac.xcworkspace")
  else
    PROJECT_ARGS=(-project "$ROOT/CoreMac.xcodeproj")
  fi

  xcodebuild \
    "${PROJECT_ARGS[@]}" \
    -scheme Core-Mac \
    -configuration Release \
    -derivedDataPath "$BUILD/DerivedData" \
    ARCHS="arm64 x86_64" \
    ONLY_ACTIVE_ARCH=NO \
    MACOSX_DEPLOYMENT_TARGET=13.0 \
    build
else
  echo "INFO: native CoreMac Xcode target not merged yet; verification helpers installed."
fi

verify_macho() {
  local f="$1"
  local info
  info="$(file "$f")"

  if grep -q 'Mach-O' <<<"$info"; then
    local archs
    archs="$(lipo -archs "$f" 2>/dev/null || true)"
    if [[ "$archs" != *arm64* || "$archs" != *x86_64* ]]; then
      echo "ERROR: non-universal Mach-O: $f [$archs]"
      return 1
    fi
    echo "OK: $f [$archs]"
  fi
}

if [[ -d "$APP" ]]; then
  echo
  echo "Verifying bundled native code..."
  failed=0
  while IFS= read -r -d '' f; do
    if file "$f" | grep -q 'Mach-O'; then
      verify_macho "$f" || failed=1
    fi
  done < <(find "$APP" -type f -print0)

  if (( failed )); then
    echo "ERROR: Core-Mac contains one or more architecture-incomplete binaries."
    exit 2
  fi

  echo "Universal 2 verification passed."
fi
