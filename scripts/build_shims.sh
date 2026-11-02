#!/usr/bin/env bash
# Build the tvOS shim libraries, the injected tvport library, and their dSYMs.
#
# Runs on the macOS Actions runner (needs the tvOS SDK). Output goes to
# out/shims/, which scripts/runner/commit_outputs.sh pushes to the outputs
# branch for the session to fetch.
#
# One dylib per shimmed library, because a two-level-namespace import names
# both a symbol and the library it must come from: dyld resolves it against
# that library and the closure of what that library re-exports, and nowhere
# else. Merging the shims into one dylib would mean every repointed load
# command pulled in every shim.
#
# Two kinds (CLAUDE.md Phase 4):
#   proxy   the framework is on the device but is short a symbol. The dylib
#           re-exports the real framework so every other symbol Roblox imports
#           from it still resolves, and defines only what is missing.
#   standin the framework is not on the device at all. The dylib defines only
#           the symbols Roblox imports from it.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$(pwd)

TVOS_MIN="${TVOS_MIN:-27.0}"       # device major version, per CLAUDE.md Phase 5
ARCH="${ARCH:-arm64}"
OUT="$ROOT/out/shims"
OBJ="$ROOT/out/shims/.obj"

DEVICE_EXPORTS="${DEVICE_EXPORTS:-$ROOT/evidence/exports/device_24K122}"
SDK=$(xcrun --sdk appletvos --show-sdk-path)
SDK_VER=$(xcrun --sdk appletvos --show-sdk-version)
CLANG=$(xcrun --sdk appletvos -f clang)
DSYMUTIL=$(xcrun -f dsymutil)
TARGET="${ARCH}-apple-tvos${TVOS_MIN}"

rm -rf "$OUT"
mkdir -p "$OUT" "$OBJ"

echo "=== shim build ==="
echo "tvOS SDK   : $SDK_VER"
echo "SDK path   : $SDK"
echo "target     : $TARGET"
echo "clang      : $CLANG"
"$CLANG" --version | head -1

BUILD_ID="${TVPORT_BUILD_ID:-$(git rev-parse --short HEAD 2>/dev/null || echo unknown)}"
echo "build id   : $BUILD_ID"

# ------------------------------------------------- stubs for private-on-tvOS
# CoreTelephony and PushKit exist on the device but the tvOS SDK ships no .tbd
# for either, so ld has nothing to re-export against. The stubs are generated
# from the device's own export lists (evidence source 1) and carry the real
# install name, so at runtime dyld re-exports the framework on the device.
GEN="$OUT/.tbd"
mkdir -p "$GEN"
gen_tbd() {
  local install="$1" listfile="$2" out="$3"
  if [ -f "$SDK/${install#/}.tbd" ]; then
    echo "    $install: using the SDK's own stub"
    echo "$SDK/${install#/}.tbd"
    return
  fi
  if [ ! -f "$listfile" ]; then
    echo "::error::no SDK stub and no device export list for $install ($listfile)" >&2
    return 1
  fi
  python3 "$ROOT/scripts/runner/make_tbd.py" "$install" "$listfile" "$out" >&2
  echo "$out"
}

echo
echo "--- stubs for frameworks the tvOS SDK does not ship"
CORETELEPHONY_TBD=$(gen_tbd \
  "/System/Library/Frameworks/CoreTelephony.framework/CoreTelephony" \
  "$DEVICE_EXPORTS/System__Library__Frameworks__CoreTelephony.framework__CoreTelephony.txt" \
  "$GEN/CoreTelephony.tbd")
PUSHKIT_TBD=$(gen_tbd \
  "/System/Library/Frameworks/PushKit.framework/PushKit" \
  "$DEVICE_EXPORTS/System__Library__Frameworks__PushKit.framework__PushKit.txt" \
  "$GEN/PushKit.tbd")
echo "    CoreTelephony stub: $CORETELEPHONY_TBD"
echo "    PushKit stub      : $PUSHKIT_TBD"

COMMON=(
  -target "$TARGET"
  -isysroot "$SDK"
  -fobjc-arc
  -fmodules -fmodule-name=tvport
  -g
  -O2
  -Wall
  -Wno-unused-parameter
  -Wno-unused-variable
  -fvisibility=default
  -DTVPORT_BUILD_ID="\"$BUILD_ID\""
)

# ---------------------------------------------------------------- libtvport
# The injected library: logging (Phase 7), the Siri Remote cursor (Phase 6)
# and the platform-identity shim. Everything else links against it, so it is
# built first and must not depend on any shim.
echo
echo "--- libtvport.dylib"
TVPORT_SRCS=(tvlog/tvlog.m)
for f in cursor/*.m shim/platform_identity.m; do
  [ -f "$f" ] && TVPORT_SRCS+=("$f")
done
echo "    sources: ${TVPORT_SRCS[*]}"
"$CLANG" "${COMMON[@]}" -dynamiclib \
  -install_name "@rpath/libtvport.dylib" \
  -compatibility_version 1.0 -current_version 1.0 \
  -framework Foundation -framework UIKit -framework GameController \
  -framework QuartzCore -framework CoreGraphics \
  -o "$OUT/libtvport.dylib" \
  "${TVPORT_SRCS[@]}"

# ---------------------------------------------------------------- shim dylibs
# name|define|extra link flags
#
# The extra flags are what make a proxy a proxy: -reexport_framework /
# -reexport_library puts the real library in our dylib's re-export list, so
# dyld's two-level lookup walks through us into it.
SHIMS=(
  "UIKit|TVPORT_SHIM_UIKIT_UNUSED|-Wl,-reexport_framework,UIKit -framework UIKit"
  "CoreFoundation|TVPORT_SHIM_COREFOUNDATION|-Wl,-reexport_framework,CoreFoundation -framework CoreFoundation"
  "CoreTelephony|TVPORT_SHIM_CORETELEPHONY|-Wl,-reexport_library,$CORETELEPHONY_TBD"
  "PushKit|TVPORT_SHIM_PUSHKIT|-Wl,-reexport_library,$PUSHKIT_TBD"
  "libswiftDarwin|TVPORT_SHIM_SWIFTDARWIN|-Wl,-reexport_library,$SDK/usr/lib/swift/libswiftDarwin.tbd"
  "MessageUI|TVPORT_SHIM_MESSAGEUI_UNUSED|"
  "SafariServices|TVPORT_SHIM_SAFARISERVICES_UNUSED|"
  "PDFKit|TVPORT_SHIM_PDFKIT_UNUSED|"
  "WidgetKit|TVPORT_SHIM_WIDGETKIT|"
  "FileProvider|TVPORT_SHIM_FILEPROVIDER|"
  "libswiftCallKit|TVPORT_SHIM_SWIFTCALLKIT|"
  "libswiftCoreMIDI|TVPORT_SHIM_SWIFTCOREMIDI|"
  "libswiftDataDetection|TVPORT_SHIM_SWIFTDATADETECTION|"
  "libswiftFileProvider|TVPORT_SHIM_SWIFTFILEPROVIDER|"
  "libswiftPassKit|TVPORT_SHIM_SWIFTPASSKIT|"
  "QuickLook|TVPORT_SHIM_QUICKLOOK|"
  "iAd|TVPORT_SHIM_IAD|"
  "EventKitUI|TVPORT_SHIM_EVENTKITUI|"
)

# Which source file carries each shim's implementation. The one-symbol shims
# all live in shim/symbols.m behind -D guards; the class stand-ins have their
# own files because they are substantial.
src_for() {
  case "$1" in
    UIKit)          echo "shim/proxy_UIKit.m" ;;
    MessageUI)      echo "shim/standin_MessageUI.m" ;;
    SafariServices) echo "shim/standin_SafariServices.m" ;;
    PDFKit)         echo "shim/standin_PDFKit.m" ;;
    *)              echo "shim/symbols.m" ;;
  esac
}

built=()
for entry in "${SHIMS[@]}"; do
  IFS='|' read -r name define extra <<<"$entry"
  src=$(src_for "$name")
  dylib="$OUT/libtvportshim-$name.dylib"
  echo
  echo "--- libtvportshim-$name.dylib  (from $src)"
  # shellcheck disable=SC2086
  "$CLANG" "${COMMON[@]}" -dynamiclib \
    "-D$define" \
    -install_name "@rpath/libtvportshim-$name.dylib" \
    -compatibility_version 1.0 -current_version 1.0 \
    -framework Foundation \
    -L"$OUT" -ltvport \
    -Wl,-rpath,@executable_path/Frameworks \
    -Wl,-rpath,@loader_path \
    $extra \
    -o "$dylib" \
    "$src"
  built+=("$dylib")
done

# -------------------------------------------------------------------- dSYMs
echo
echo "--- dSYMs"
for f in "$OUT"/*.dylib; do
  "$DSYMUTIL" "$f" -o "$f.dSYM" >/dev/null
  echo "    $(basename "$f").dSYM"
done

# ------------------------------------------------------------------ manifest
echo
echo "--- verifying and writing the manifest"
python3 - "$OUT" "$SDK_VER" "$TARGET" "$BUILD_ID" <<'PY'
import json, subprocess, sys, pathlib

out = pathlib.Path(sys.argv[1]); sdk_ver, target, build_id = sys.argv[2:5]
manifest = {"sdk_version": sdk_ver, "target": target, "build_id": build_id,
            "libraries": {}}
ok = True
for dylib in sorted(out.glob("*.dylib")):
    def run(*a):
        return subprocess.run(a, capture_output=True, text=True).stdout
    otool = run("otool", "-l", str(dylib))
    nm = run("nm", "-gU", str(dylib))
    exports = sorted(l.split()[-1] for l in nm.splitlines()
                     if l.strip() and not l.startswith("/"))
    reexports, install, platform, minos = [], None, None, None
    lines = otool.splitlines()
    for i, l in enumerate(lines):
        s = l.strip()
        if s == "cmd LC_REEXPORT_DYLIB":
            for j in range(i, min(i + 8, len(lines))):
                if "name " in lines[j]:
                    reexports.append(lines[j].strip().split("name ", 1)[1].split(" (")[0])
                    break
        if s == "cmd LC_ID_DYLIB":
            for j in range(i, min(i + 8, len(lines))):
                if "name " in lines[j]:
                    install = lines[j].strip().split("name ", 1)[1].split(" (")[0]
                    break
        if s.startswith("platform "):
            platform = s.split()[-1]
        if s.startswith("minos "):
            minos = s.split()[-1]
    rec = {"install_name": install, "platform": platform, "minos": minos,
           "reexports": reexports, "export_count": len(exports),
           "exports": exports[:200]}
    manifest["libraries"][dylib.name] = rec
    # platform must be tvOS (3) or the device will refuse to load it
    if platform not in ("3", "TVOS", "tvos"):
        print(f"::warning::{dylib.name}: platform is {platform!r}, expected tvOS")
    print(f"  {dylib.name:44} platform={platform} minos={minos} "
          f"exports={len(exports)} reexports={len(reexports)}")
(out / "manifest.json").write_text(json.dumps(manifest, indent=1))
sys.exit(0 if ok else 1)
PY

rm -rf "$OBJ"
echo
echo "=== built ${#built[@]} shim dylibs + libtvport.dylib into $OUT ==="
ls -la "$OUT"
