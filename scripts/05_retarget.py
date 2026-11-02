#!/usr/bin/env python3
"""Phase 5: retarget Roblox's Mach-Os from iOS to tvOS and repoint load commands.

Runs in the session only. Operates on a *copy* of the pristine bundle
(work/unpacked -> work/patched), so work/unpacked stays as unpacked and input/
is never touched.

Per CLAUDE.md the patches are limited to:
  * removing the code signature,
  * setting LC_BUILD_VERSION to platform tvOS with the device minimum OS and
    the tvOS SDK the shims were built against,
  * repointing load commands for missing or proxied libraries at the shim
    dylibs in Frameworks/,
  * adding a load command for the injected logging/cursor library,
  * removing unsupported app extensions,
  * Info.plist and entitlements.

Nothing in Roblox's own code, data or assets is altered.

Two older binaries (Backtrace, Backtrace_PLCrashReporter, libswift_Concurrency)
carry LC_VERSION_MIN_IPHONEOS rather than LC_BUILD_VERSION. LIEF cannot convert
one load command into another in place, so those are handled by rewriting the
command's bytes directly: LC_VERSION_MIN_IPHONEOS (0x25) and LC_VERSION_MIN_TVOS
(0x2F) have the identical layout (cmd, cmdsize, version, sdk), so only the
command id changes.
"""
from __future__ import annotations

import json
import plistlib
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lief  # noqa: E402
from lib_macho import (EVIDENCE, ROOT, WORK, app_dir, build_version,  # noqa: E402
                       find_machos, parse, rel)

PATCHED = WORK / "patched"

LC_VERSION_MIN_IPHONEOS = 0x25
LC_VERSION_MIN_TVOS = 0x2F
LC_BUILD_VERSION = 0x32
PLATFORM_TVOS = 3

# App extensions to remove, with the reason recorded for the log.
EXTENSIONS_TO_REMOVE = {
    "RBNotificationServiceExtension.appex": (
        "com.apple.usernotifications.service",
        "A UNNotificationServiceExtension only runs to mutate an incoming push "
        "payload, which requires the aps-environment entitlement. A free Apple "
        "developer account cannot provision that, so the extension can never be "
        "invoked; and tvOS does not host this extension point."),
    "RBWidgetExtension.appex": (
        "com.apple.widgetkit-extension",
        "A WidgetKit extension draws Home Screen / Lock Screen widgets. "
        "WidgetKit.framework is absent from the tvOS 27.2 device cache "
        "altogether (evidence source 1), and tvOS has no widgets, so the "
        "extension cannot load. Removing it also drops 46 of the 49 unresolved "
        "WidgetKit symbols."),
}


def load_shim_map():
    """library install name -> shim dylib filename, from the shim manifest."""
    mapping = EVIDENCE / "queries" / "shim_map.json"
    if not mapping.is_file():
        raise SystemExit(
            f"{mapping} is missing. It records which library each shim stands in "
            f"for; Phase 5 cannot repoint load commands without it.")
    return json.loads(mapping.read_text())


def strip_signature(b) -> bool:
    """Remove LC_CODE_SIGNATURE. The IPA ships unsigned; the user's signing
    tool adds a fresh signature, and a stale one would not survive the edits
    below anyway."""
    for c in list(b.commands):
        if c.command == lief.MachO.LoadCommand.TYPE.CODE_SIGNATURE:
            b.remove(c)
            return True
    return False


def set_tvos_build_version(path: Path, minos: str, sdk: str, log: list) -> str:
    """Set the platform to tvOS, returning what was done.

    LIEF rewrites LC_BUILD_VERSION cleanly. For the two frameworks that still
    use LC_VERSION_MIN_IPHONEOS the command id is patched in the file bytes,
    because the two commands have the same shape and LIEF offers no conversion.
    """
    b = parse(path)[0]
    bv = build_version(b)
    if bv is None:
        return "no version load command"

    def ver_tuple(s):
        parts = [int(x) for x in s.split(".")]
        while len(parts) < 3:
            parts.append(0)
        return parts[:3]

    if "VERSION_MIN" in str(bv.get("platform", "")):
        # patch the command id in place: 0x25 -> 0x2F
        data = bytearray(path.read_bytes())
        hdr = struct.unpack_from("<IIIIIII", data, 0)
        ncmds, sizeofcmds = hdr[4], hdr[5]
        off = 32  # mach_header_64
        changed = False
        for _ in range(ncmds):
            cmd, cmdsize = struct.unpack_from("<II", data, off)
            if cmd == LC_VERSION_MIN_IPHONEOS:
                struct.pack_into("<I", data, off, LC_VERSION_MIN_TVOS)
                mn = ver_tuple(minos)
                sd = ver_tuple(sdk)
                packed_min = (mn[0] << 16) | (mn[1] << 8) | mn[2]
                packed_sdk = (sd[0] << 16) | (sd[1] << 8) | sd[2]
                struct.pack_into("<II", data, off + 8, packed_min, packed_sdk)
                changed = True
                break
            off += cmdsize
        if changed:
            path.write_bytes(bytes(data))
            log.append(f"LC_VERSION_MIN_IPHONEOS -> LC_VERSION_MIN_TVOS, "
                       f"minos {bv['minos']} -> {minos}, sdk {bv['sdk']} -> {sdk}")
            return "version_min patched"
        return "version_min not found despite being reported"

    # LC_BUILD_VERSION: rewrite through LIEF
    for c in b.commands:
        if c.command == lief.MachO.LoadCommand.TYPE.BUILD_VERSION:
            before = (str(c.platform), ".".join(map(str, c.minos)), ".".join(map(str, c.sdk)))
            c.platform = lief.MachO.BuildVersion.PLATFORMS.TVOS
            c.minos = ver_tuple(minos)
            c.sdk = ver_tuple(sdk)
            b.write(str(path))
            log.append(f"LC_BUILD_VERSION platform {before[0]} -> TVOS, "
                       f"minos {before[1]} -> {minos}, sdk {before[2]} -> {sdk}")
            return "build_version patched"
    return "build_version not found"


def repoint_and_inject(path: Path, shim_map: dict, inject: str | None, log: list):
    """Repoint shimmed libraries and add the injected library's load command."""
    b = parse(path)[0]
    changed = False
    for libcmd in b.libraries:
        if "ID_DYLIB" in str(libcmd.command):
            continue
        target = shim_map.get(libcmd.name)
        if target:
            old = libcmd.name
            libcmd.name = target
            log.append(f"load command {old} -> {target}")
            changed = True
    if inject:
        have = any(l.name == inject for l in b.libraries)
        if not have:
            b.add_library(inject)
            log.append(f"added LC_LOAD_DYLIB {inject}")
            changed = True
    if changed:
        b.write(str(path))
    return changed


def ensure_rpath(path: Path, needed: list, log: list):
    """Make sure @rpath can find the shims in Frameworks/.

    The shims' install names are @rpath/..., so every binary that now links
    them needs an LC_RPATH that reaches the app's Frameworks directory. Most of
    Roblox's binaries already have @executable_path/Frameworks; RobloxLib and
    libswift_Concurrency have no LC_RPATH at all.
    """
    b = parse(path)[0]
    existing = {c.path for c in b.commands
                if c.command == lief.MachO.LoadCommand.TYPE.RPATH}
    added = False
    for rp in needed:
        if rp not in existing:
            b.add(lief.MachO.RPathCommand.create(rp))
            log.append(f"added LC_RPATH {rp}")
            added = True
    if added:
        b.write(str(path))
    return added


def patch_info_plist(app: Path, tvos_min: str, log: list):
    """Info.plist changes, each with the reason recorded."""
    p = app / "Info.plist"
    data = plistlib.loads(p.read_bytes())
    before = dict(data)

    data["UIDeviceFamily"] = [3]                       # 3 = Apple TV
    data["CFBundleSupportedPlatforms"] = ["AppleTVOS"]
    data["DTPlatformName"] = "appletvos"
    data["MinimumOSVersion"] = tvos_min

    # iOS-only keys. Each is removed because it either cannot apply on tvOS or
    # names a capability the device does not have.
    REMOVE = {
        "LSRequiresIPhoneOS": "iOS-only launch gate",
        "UIRequiresFullScreen": "iPad multitasking only",
        "UISupportedInterfaceOrientations": "tvOS has no device orientation",
        "UISupportedInterfaceOrientations~ipad": "tvOS has no device orientation",
        "UIStatusBarStyle": "tvOS has no status bar",
        "UIViewControllerBasedStatusBarAppearance": "tvOS has no status bar",
        "UIStatusBarHidden": "tvOS has no status bar",
        "CFBundleIcons": "iOS icon dictionary; tvOS uses a layered brand asset",
        "CFBundleIcons~ipad": "iOS icon dictionary",
        "CFBundleIconFiles": "iOS icon list",
        "CFBundleIconName": "iOS icon name; tvOS resolves its icon differently",
        "UIRequiredDeviceCapabilities": "names iOS-only capabilities",
        "UIApplicationShortcutItems": "Home Screen quick actions are iOS-only",
        "UIBackgroundModes": "the remaining modes (remote-notification, voip, "
                             "fetch) need entitlements a free account cannot "
                             "have, and voip has no meaning on tvOS",
        "UIFileSharingEnabled": "iTunes file sharing is iOS-only",
        "UISupportsDocumentBrowser": "no document browser on tvOS",
        "NSSupportsAutomaticTermination": "macOS key",
        "UIPrerenderedIcon": "iOS icon key",
    }
    removed = []
    for key, reason in REMOVE.items():
        if key in data:
            del data[key]
            removed.append((key, reason))

    # Our own keys, so behaviour can be changed without a rebuild.
    data.setdefault("TVPortLogLevel", "verbose")
    data.setdefault("TVPortPlatformIdentity", "pad")

    p.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_XML))

    log.append(f"UIDeviceFamily {before.get('UIDeviceFamily')} -> [3]")
    log.append(f"CFBundleSupportedPlatforms {before.get('CFBundleSupportedPlatforms')} "
               f"-> ['AppleTVOS']")
    log.append(f"DTPlatformName {before.get('DTPlatformName')} -> appletvos")
    log.append(f"MinimumOSVersion {before.get('MinimumOSVersion')} -> {tvos_min}")
    for key, reason in removed:
        log.append(f"removed {key}: {reason}")
    log.append("added TVPortLogLevel=verbose, TVPortPlatformIdentity=pad")
    return removed


def remove_extensions(app: Path, log: list):
    plugins = app / "PlugIns"
    removed = []
    if not plugins.is_dir():
        return removed
    for name, (point, reason) in EXTENSIONS_TO_REMOVE.items():
        d = plugins / name
        if d.is_dir():
            shutil.rmtree(d)
            removed.append(name)
            log.append(f"removed PlugIns/{name} ({point}): {reason}")
    # drop PlugIns entirely if it is now empty
    if plugins.is_dir() and not any(plugins.iterdir()):
        plugins.rmdir()
        log.append("removed the now-empty PlugIns directory")
    return removed


def is_ours(rel_path: str) -> bool:
    """True for the dylibs this project builds.

    They must be excluded from the patch loop. They are already tvOS-targeted,
    they already link libtvport, and repointing their load commands turns a
    proxy's re-export of the real framework into a re-export of itself.
    """
    name = rel_path.rsplit("/", 1)[-1]
    return name == "libtvport.dylib" or name.startswith("libtvportshim-")


def install_shims(app: Path, log: list):
    """Copy the built shim dylibs into the bundle's Frameworks directory.

    Their install names are @rpath/..., and every binary that now links them
    gets an LC_RPATH reaching Frameworks/ (see ensure_rpath), so this is where
    dyld will look. dSYMs stay in out/ and are shipped separately: they are for
    the laptop's debugger, not for the device.
    """
    shims = ROOT / "out" / "shims"
    if not shims.is_dir():
        raise SystemExit(
            f"{shims} is missing. Run the build-shims workflow and fetch it with "
            f"scripts/fetch_outputs.sh; Phase 5 cannot repoint load commands at "
            f"dylibs that are not there.")
    frameworks = app / "Frameworks"
    frameworks.mkdir(exist_ok=True)
    copied = []
    for src in sorted(shims.glob("*.dylib")):
        shutil.copy2(src, frameworks / src.name)
        copied.append(src.name)
        log.append(f"installed Frameworks/{src.name}")
    if not copied:
        raise SystemExit(f"no .dylib files in {shims}")
    print(f"installed {len(copied)} dylibs into Frameworks/")
    return copied


def main():
    tvos_min = "27.0"
    tvos_sdk = "27.2"
    inject = "@rpath/libtvport.dylib"

    shim_map = load_shim_map()

    src_app = app_dir()
    if PATCHED.exists():
        shutil.rmtree(PATCHED)
    PATCHED.mkdir(parents=True)
    dst_app = PATCHED / "Payload" / src_app.name
    dst_app.parent.mkdir(parents=True)
    print(f"copying {src_app} -> {dst_app}")
    shutil.copytree(src_app, dst_app, symlinks=True)

    log: list[str] = []
    log.append(f"# Phase 5 patch log")
    log.append(f"target: platform tvOS, minos {tvos_min}, sdk {tvos_sdk}")
    log.append(f"injected library: {inject}")
    log.append("")

    log.append("## App extensions")
    remove_extensions(dst_app, log)
    log.append("")

    log.append("## Shim libraries installed into the bundle")
    install_shims(dst_app, log)
    log.append("")

    log.append("## Info.plist")
    patch_info_plist(dst_app, tvos_min, log)
    log.append("")

    log.append("## Mach-O binaries")
    log.append("")
    log.append("Our own dylibs are **not** patched. The runner already built them for "
               "tvOS 27.0, and running them through the repointing pass is actively "
               "harmful: the first attempt rewrote the UIKit proxy's own "
               "LC_REEXPORT_DYLIB for the real UIKit into a reference to itself, which "
               "would have left every UIKit symbol Roblox imports resolving into a "
               "dylib that defines one class. The verifier would have caught it, but "
               "the right fix is not to touch them at all.")
    log.append("")
    for p in find_machos(dst_app):
        r = rel(p, dst_app)
        if is_ours(r):
            log.append(f"\n### {r}")
            log.append("- skipped: built by the shim build for tvOS; not patched")
            print(f"{r}: skipped (ours)")
            continue
        per: list[str] = []
        b = parse(p)[0]
        if strip_signature(b):
            b.write(str(p))
            per.append("removed LC_CODE_SIGNATURE")
        set_tvos_build_version(p, tvos_min, tvos_sdk, per)
        repoint_and_inject(p, shim_map, inject if r != "Frameworks/libtvport.dylib" else None, per)
        ensure_rpath(p, ["@executable_path/Frameworks", "@loader_path/Frameworks"], per)
        log.append(f"\n### {r}")
        for line in per:
            log.append(f"- {line}")
        print(f"{r}: {len(per)} changes")

    out = EVIDENCE / "patch_log.md"
    out.write_text("\n".join(log) + "\n")
    print(f"\nwrote {out}")
    print(f"patched bundle at {dst_app}")


if __name__ == "__main__":
    main()
