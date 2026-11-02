#!/usr/bin/env bash
# Select the Xcode on this runner that ships a requested platform SDK version.
#
#   select_xcode.sh appletvos 27.2      # exact, else highest available
#   select_xcode.sh iphoneos  26.2
#
# Prints the chosen DEVELOPER_DIR on stdout and a short report on stderr, and
# exits non-zero only if no Xcode on the image has that platform at all.
#
# Why this exists: which SDK version a given Xcode carries differs per runner
# image (macos-26's Xcode 26.6 ships tvOS 26.5, not 26.6), so pinning an Xcode
# version does not reliably pin an SDK version. The SDK version is what the
# evidence has to be labelled with, so it is what gets selected on.
set -uo pipefail

PLATFORM="${1:?platform, e.g. appletvos or iphoneos}"
WANT="${2:-}"

best_dir=""; best_ver=""
exact_dir=""; exact_ver=""

for app in /Applications/Xcode*.app; do
  [ -d "$app" ] || continue
  dev="$app/Contents/Developer"
  vers=$(DEVELOPER_DIR="$dev" xcodebuild -showsdks 2>/dev/null \
          | grep -oE "${PLATFORM}[0-9]+(\.[0-9]+)*" | sed "s/^${PLATFORM}//" | sort -u -V)
  [ -z "$vers" ] && continue
  for v in $vers; do
    if [ -n "$WANT" ] && [ "$v" = "$WANT" ]; then
      exact_dir="$dev"; exact_ver="$v"
    fi
    if [ -z "$best_ver" ] || [ "$(printf '%s\n%s\n' "$best_ver" "$v" | sort -V | tail -1)" = "$v" ]; then
      best_ver="$v"; best_dir="$dev"
    fi
  done
done

if [ -z "$best_dir" ]; then
  echo "no Xcode on this image provides a $PLATFORM SDK" >&2
  exit 1
fi

if [ -n "$exact_dir" ]; then
  echo "selected $PLATFORM SDK $exact_ver (requested $WANT) from $exact_dir" >&2
  echo "$exact_dir"
  exit 0
fi

if [ -n "$WANT" ]; then
  echo "::warning::$PLATFORM SDK $WANT is not on this image; using the highest available, $best_ver" >&2
fi
echo "selected $PLATFORM SDK $best_ver from $best_dir" >&2
echo "$best_dir"
