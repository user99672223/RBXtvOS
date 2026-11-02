#!/usr/bin/env python3
"""Phase 8: package the unsigned tvOS IPA, the entitlements and the dSYMs.

Produces, in out/:
  Roblox-tvOS-unsigned.ipa   standard Payload/ layout, no code signature
  entitlements.plist         for a free Apple developer account
  dSYMs/                     our own dSYMs, for symbolicating on the laptop
  package_manifest.json      what went in, with sizes and checksums

The IPA is a plain zip of work/patched/Payload. Nothing is signed: the user's
signing tool re-signs it, and every embedded signature was removed in Phase 5
because the load-command edits would have invalidated them anyway.
"""
from __future__ import annotations

import hashlib
import json
import plistlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_macho import EVIDENCE, ROOT, WORK  # noqa: E402

PATCHED = WORK / "patched"
OUT = ROOT / "out"
IPA = OUT / "Roblox-tvOS-unsigned.ipa"

# Entitlements a free Apple developer account can actually be issued.
#
# A free (personal team) development profile grants application-identifier,
# team-identifier, get-task-allow and keychain-access-groups, and nothing that
# needs a paid capability. Everything Roblox originally asked for beyond that
# -- aps-environment, applesignin, associated-domains, the AutoFill credential
# provider, declared-age-range, extended-virtual-addressing,
# increased-memory-limit, storekit.request-data, the communication and
# time-sensitive notification entitlements and the app group -- cannot be
# provisioned, so asking for any of them makes signing fail rather than
# degrading gracefully. They are listed in evidence/storage_entitlements.md
# with the feature each one costs.
#
# get-task-allow is kept deliberately: it is what lets debugserver attach, and
# a free *development* profile does allow it (CLAUDE.md Phase 8).
FREE_ACCOUNT_ENTITLEMENTS = {
    "get-task-allow": True,
}

PLACEHOLDERS = {
    "application-identifier": "$(AppIdentifierPrefix)$(CFBundleIdentifier)",
    "com.apple.developer.team-identifier": "$(AppIdentifierPrefix)",
    "keychain-access-groups": ["$(AppIdentifierPrefix)$(CFBundleIdentifier)"],
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_entitlements(path: Path, include_placeholders: bool) -> dict:
    ent = dict(FREE_ACCOUNT_ENTITLEMENTS)
    if include_placeholders:
        ent.update(PLACEHOLDERS)
    path.write_bytes(plistlib.dumps(ent, fmt=plistlib.FMT_XML))
    return ent


def build_ipa(payload: Path, ipa: Path) -> int:
    """Zip Payload/ into an IPA. Stored deterministically, deflate for size."""
    if ipa.exists():
        ipa.unlink()
    count = 0
    # Sorted walk so the archive is reproducible for a given input tree.
    entries = sorted(payload.rglob("*"), key=lambda p: str(p))
    with zipfile.ZipFile(ipa, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in entries:
            arc = Path("Payload") / p.relative_to(payload)
            if p.is_symlink():
                # preserve the link rather than following it
                info = zipfile.ZipInfo(str(arc))
                info.create_system = 3
                info.external_attr = (0o120755 << 16)
                z.writestr(info, str(p.readlink()))
                count += 1
            elif p.is_dir():
                continue
            elif p.is_file():
                info = zipfile.ZipInfo(str(arc), date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                mode = 0o100755 if p.stat().st_mode & 0o111 else 0o100644
                info.external_attr = (mode << 16)
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(p, "rb") as f:
                    z.writestr(info, f.read())
                count += 1
    return count


def main():
    payload = PATCHED / "Payload"
    if not payload.is_dir():
        raise SystemExit(f"{payload} does not exist; run scripts/05_retarget.py first")

    verify = EVIDENCE / "phase5_verify.json"
    if verify.is_file():
        v = json.loads(verify.read_text())["totals"]
        if v.get("blockers") or v.get("unloadable_non_weak_libraries"):
            print(f"::warning:: Phase 5 verification is not clean "
                  f"({v['blockers']} unresolved imports, "
                  f"{v['unloadable_non_weak_libraries']} unloadable libraries). "
                  f"Packaging anyway so the build can be inspected, but it is not "
                  f"expected to launch.")
    else:
        print("::warning:: no phase5_verify.json; package is unverified")

    OUT.mkdir(parents=True, exist_ok=True)

    print(f"zipping {payload} -> {IPA}")
    n = build_ipa(payload, IPA)
    print(f"  {n} entries, {IPA.stat().st_size:,} bytes")

    ent_path = OUT / "entitlements.plist"
    ent = write_entitlements(ent_path, include_placeholders=True)
    print(f"wrote {ent_path}: {sorted(ent)}")

    # dSYMs for our own code, so LLDB on the laptop can symbolicate our frames.
    dsym_out = OUT / "dSYMs"
    if dsym_out.exists():
        shutil.rmtree(dsym_out)
    dsym_out.mkdir(parents=True)
    copied = []
    for d in sorted((OUT / "shims").glob("*.dSYM")):
        shutil.copytree(d, dsym_out / d.name)
        copied.append(d.name)
    print(f"copied {len(copied)} dSYM bundles into {dsym_out}")

    app = sorted(payload.glob("*.app"))[0]
    info = plistlib.loads((app / "Info.plist").read_bytes())
    machos = sorted(p.name for p in (app / "Frameworks").glob("libtvport*.dylib"))

    manifest = {
        "ipa": {"path": str(IPA.relative_to(ROOT)), "bytes": IPA.stat().st_size,
                "entries": n, "sha256": sha256(IPA), "signed": False},
        "entitlements": {"path": str(ent_path.relative_to(ROOT)), "keys": sorted(ent)},
        "dsyms": copied,
        "bundle": {
            "name": app.name,
            "CFBundleIdentifier": info.get("CFBundleIdentifier"),
            "CFBundleShortVersionString": info.get("CFBundleShortVersionString"),
            "CFBundleVersion": info.get("CFBundleVersion"),
            "MinimumOSVersion": info.get("MinimumOSVersion"),
            "UIDeviceFamily": info.get("UIDeviceFamily"),
            "CFBundleSupportedPlatforms": info.get("CFBundleSupportedPlatforms"),
            "DTPlatformName": info.get("DTPlatformName"),
            "TVPortLogLevel": info.get("TVPortLogLevel"),
            "TVPortPlatformIdentity": info.get("TVPortPlatformIdentity"),
        },
        "injected_and_shim_dylibs": machos,
        "phase5": json.loads(verify.read_text())["totals"] if verify.is_file() else None,
    }
    (OUT / "package_manifest.json").write_text(json.dumps(manifest, indent=1))

    print()
    print("=" * 66)
    print(f"IPA        : {IPA}")
    print(f"  size     : {IPA.stat().st_size:,} bytes ({n} entries), unsigned")
    print(f"  sha256   : {manifest['ipa']['sha256']}")
    print(f"entitlements: {ent_path}")
    print(f"dSYMs      : {len(copied)} bundles in {dsym_out}")
    print(f"bundle     : {info.get('CFBundleIdentifier')} "
          f"{info.get('CFBundleShortVersionString')} "
          f"({info.get('CFBundleVersion')}), "
          f"minOS {info.get('MinimumOSVersion')}, "
          f"family {info.get('UIDeviceFamily')}, "
          f"{info.get('CFBundleSupportedPlatforms')}")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
