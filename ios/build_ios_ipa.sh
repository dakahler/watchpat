#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_PATH="$ROOT_DIR/ios/WatchPATiOS.xcodeproj"
SCHEME="WatchPATiOS"
CONFIGURATION="Release"
TEAM_ID="${APPLE_TEAM_ID:-}"
BUNDLE_ID="${WATCHPAT_IOS_BUNDLE_ID:-com.watchpat.ios}"
EXPORT_METHOD="${WATCHPAT_IOS_EXPORT_METHOD:-ad-hoc}"
EXPORT_PATH="${WATCHPAT_IOS_EXPORT_PATH:-$ROOT_DIR/ios/build/export}"
ARCHIVE_PATH="${WATCHPAT_IOS_ARCHIVE_PATH:-$ROOT_DIR/ios/build/WatchPATiOS.xcarchive}"
DERIVED_DATA_PATH="${WATCHPAT_IOS_DERIVED_DATA_PATH:-/tmp/WatchPATiOSDerivedDataArchive}"
CODE_SIGN_IDENTITY="${WATCHPAT_IOS_CODE_SIGN_IDENTITY:-}"
PROVISIONING_PROFILE_SPECIFIER="${WATCHPAT_IOS_PROVISIONING_PROFILE_SPECIFIER:-}"
APPLE_ID="${WATCHPAT_IOS_APPLE_ID:-}"

usage() {
  cat <<EOF
Usage:
  APPLE_TEAM_ID=ABCDE12345 ios/build_ios_ipa.sh

Optional environment variables:
  WATCHPAT_IOS_BUNDLE_ID                    Override bundle id (default: com.watchpat.ios)
  WATCHPAT_IOS_EXPORT_METHOD                ad-hoc | app-store | development | enterprise (default: ad-hoc)
  WATCHPAT_IOS_EXPORT_PATH                  Output folder for exported IPA
  WATCHPAT_IOS_ARCHIVE_PATH                 Archive output path
  WATCHPAT_IOS_DERIVED_DATA_PATH            DerivedData path
  WATCHPAT_IOS_CODE_SIGN_IDENTITY           Optional explicit signing identity
  WATCHPAT_IOS_PROVISIONING_PROFILE_SPECIFIER Optional explicit provisioning profile name
  WATCHPAT_IOS_APPLE_ID                     Optional Apple ID for ExportOptions manifest

Examples:
  APPLE_TEAM_ID=ABCDE12345 WATCHPAT_IOS_EXPORT_METHOD=development ios/build_ios_ipa.sh
  APPLE_TEAM_ID=ABCDE12345 WATCHPAT_IOS_BUNDLE_ID=com.example.watchpat ios/build_ios_ipa.sh
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ -z "$TEAM_ID" ]]; then
  echo "error: APPLE_TEAM_ID is required" >&2
  usage
  exit 1
fi

mkdir -p "$(dirname "$ARCHIVE_PATH")" "$EXPORT_PATH"

EXPORT_OPTIONS_PLIST="$(mktemp /tmp/watchpat_export_options.XXXXXX.plist)"
cleanup() {
  rm -f "$EXPORT_OPTIONS_PLIST"
}
trap cleanup EXIT

cat > "$EXPORT_OPTIONS_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>method</key>
  <string>${EXPORT_METHOD}</string>
  <key>signingStyle</key>
  <string>automatic</string>
  <key>teamID</key>
  <string>${TEAM_ID}</string>
  <key>destination</key>
  <string>export</string>
  <key>stripSwiftSymbols</key>
  <true/>
  <key>compileBitcode</key>
  <false/>
</dict>
</plist>
EOF

BUILD_ARGS=(
  -project "$PROJECT_PATH"
  -scheme "$SCHEME"
  -configuration "$CONFIGURATION"
  -destination "generic/platform=iOS"
  -derivedDataPath "$DERIVED_DATA_PATH"
  -archivePath "$ARCHIVE_PATH"
  DEVELOPMENT_TEAM="$TEAM_ID"
  PRODUCT_BUNDLE_IDENTIFIER="$BUNDLE_ID"
  CODE_SIGN_STYLE=Automatic
)

if [[ -n "$CODE_SIGN_IDENTITY" ]]; then
  BUILD_ARGS+=(CODE_SIGN_IDENTITY="$CODE_SIGN_IDENTITY")
fi

if [[ -n "$PROVISIONING_PROFILE_SPECIFIER" ]]; then
  BUILD_ARGS+=(PROVISIONING_PROFILE_SPECIFIER="$PROVISIONING_PROFILE_SPECIFIER")
fi

echo "Archiving $SCHEME..."
xcodebuild "${BUILD_ARGS[@]}" archive

echo "Exporting IPA to $EXPORT_PATH..."
xcodebuild -exportArchive \
  -archivePath "$ARCHIVE_PATH" \
  -exportPath "$EXPORT_PATH" \
  -exportOptionsPlist "$EXPORT_OPTIONS_PLIST"

echo
echo "Archive:"
echo "  $ARCHIVE_PATH"
echo
echo "Exported files:"
find "$EXPORT_PATH" -maxdepth 2 -type f | sed 's/^/  /'

