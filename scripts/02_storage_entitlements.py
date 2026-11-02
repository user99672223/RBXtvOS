#!/usr/bin/env python3
"""Phase 2.5 and 2.6: where Roblox writes, and what it loses without entitlements.

Storage (2.5)
    tvOS does not give an app persistent local storage the way iOS does: the
    Documents directory is not part of the tvOS container contract, and Caches
    is purgeable at the system's discretion. This records which directory APIs
    each binary actually calls and which container-relative path strings it
    carries, so the report can say what will and will not survive, and so any
    redirect is aimed at something real rather than guessed at.

Entitlements (2.6)
    The original entitlements come from the embedded code signature (recorded
    in Phase 1). A free Apple developer account cannot have most of them. This
    pairs each one with the frameworks and symbols Roblox imports for it, so
    the report can say which feature stops working rather than just which
    entitlement is gone.
"""
from __future__ import annotations

import json
import plistlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_macho import EVIDENCE, app_dir, entitlements, find_machos, parse, rel  # noqa: E402

# Directory-lookup APIs. Presence of the import is hard evidence the binary
# asks for that location; the string scan then shows what it builds under it.
STORAGE_APIS = {
    "_NSSearchPathForDirectoriesInDomains": "Foundation directory lookup",
    "_NSHomeDirectory": "container root",
    "_NSTemporaryDirectory": "tmp (never preserved)",
    "_NSUserDefaults": "preferences",
    "_CFPreferencesCopyAppValue": "preferences (CF)",
    "_CFPreferencesSetAppValue": "preferences (CF)",
    "_sqlite3_open": "sqlite database",
    "_sqlite3_open_v2": "sqlite database",
    "_open": "raw file open",
    "_fopen": "raw file open",
    "_mkdir": "directory creation",
    "_NSFileManager": "file manager",
}

# Container-relative path fragments worth reporting.
PATH_HINTS = (
    "Library/Caches", "Library/Application Support", "Library/Preferences",
    "Documents/", "/Documents", "tmp/", "/tmp", ".sqlite", ".db", ".log",
    "Caches/", "Application Support", "/var/mobile", "NSDocumentDirectory",
    "NSCachesDirectory", "NSApplicationSupportDirectory", "NSLibraryDirectory",
)

# Entitlements a free account cannot provision, and what each one buys.
FREE_ACCOUNT_IMPOSSIBLE = {
    "aps-environment": ("Push notifications (APNs).",
                        ["UserNotifications", "PushKit"]),
    "com.apple.developer.associated-domains": (
        "Universal links and web-credential autofill.", []),
    "com.apple.developer.applesignin": ("Sign in with Apple.",
                                        ["AuthenticationServices"]),
    "com.apple.developer.authentication-services.autofill-credential-provider": (
        "Password AutoFill extension.", ["AuthenticationServices"]),
    "com.apple.developer.devicecheck.appattest-environment": (
        "App Attest device integrity.", ["DeviceCheck"]),
    "com.apple.developer.declared-age-range": (
        "Declared age range (age assurance).", ["DeclaredAgeRange"]),
    "com.apple.developer.storekit.request-data": (
        "StoreKit external purchase / request data.", ["StoreKit"]),
    "com.apple.developer.usernotifications.communication": (
        "Communication notifications.", ["UserNotifications"]),
    "com.apple.developer.usernotifications.time-sensitive": (
        "Time-sensitive notifications.", ["UserNotifications"]),
    "com.apple.security.application-groups": (
        "App group container shared with the extensions.", []),
    "com.apple.developer.kernel.extended-virtual-addressing": (
        "Extended virtual addressing (>4 GB address space).", []),
    "com.apple.developer.kernel.increased-memory-limit": (
        "Raised memory limit.", []),
    "com.apple.developer.icloud-container-identifiers": ("iCloud containers.", []),
    "com.apple.developer.icloud-services": ("iCloud services.", []),
    "com.apple.developer.in-app-payments": ("Apple Pay.", ["PassKit"]),
    "com.apple.developer.networking.multipath": ("Multipath TCP.", []),
    "com.apple.developer.networking.wifi-info": ("Wi-Fi SSID access.", []),
    "com.apple.developer.siri": ("SiriKit.", ["Intents"]),
}

# Entitlements a free development profile does grant.
FREE_ACCOUNT_OK = {
    "get-task-allow": "Debugger attach. Kept deliberately (Phase 8).",
    "application-identifier": "Filled in by the signing tool.",
    "com.apple.developer.team-identifier": "Filled in by the signing tool.",
    "keychain-access-groups": "Keychain, rewritten by the signing tool.",
}


def strings_with_hints(b):
    out = set()
    for s in b.sections:
        if s.name not in ("__cstring", "__const", "__cfstring", "__oslogstring",
                          "__objc_methname", "__data"):
            continue
        try:
            data = bytes(s.content)
        except Exception:
            continue
        for raw in data.split(b"\0"):
            if not (3 <= len(raw) <= 300):
                continue
            try:
                t = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if any(h in t for h in PATH_HINTS):
                out.add(t)
    return out


def main():
    app = app_dir()
    storage = {}
    for p in find_machos(app):
        r = rel(p, app)
        b = parse(p)[0]
        imported = {s.name for s in b.imported_symbols}
        apis = {k: v for k, v in STORAGE_APIS.items() if k in imported}
        hints = sorted(strings_with_hints(b))
        storage[r] = {
            "directory_apis_imported": apis,
            "path_strings": hints[:400],
            "path_strings_total": len(hints),
        }
        print(f"{r}: storage APIs={len(apis)} path-ish strings={len(hints)}")

    # entitlements of the main executable and each extension
    ents = {}
    for p in find_machos(app):
        r = rel(p, app)
        try:
            e = entitlements(p)
        except Exception as exc:
            e = {"_error": f"{type(exc).__name__}: {exc}"}
        if e:
            ents[r] = e

    analysis = {}
    for binary, e in ents.items():
        if not isinstance(e, dict):
            continue
        rows = []
        for key, value in sorted(e.items()):
            if key in FREE_ACCOUNT_IMPOSSIBLE:
                desc, fw = FREE_ACCOUNT_IMPOSSIBLE[key]
                rows.append({"entitlement": key, "value": _plain(value),
                             "free_account": "NOT AVAILABLE", "feature": desc,
                             "related_frameworks": fw})
            elif key in FREE_ACCOUNT_OK:
                rows.append({"entitlement": key, "value": _plain(value),
                             "free_account": "available",
                             "feature": FREE_ACCOUNT_OK[key],
                             "related_frameworks": []})
            else:
                rows.append({"entitlement": key, "value": _plain(value),
                             "free_account": "UNVERIFIED", "feature": "",
                             "related_frameworks": []})
        analysis[binary] = rows

    out = {"storage": storage, "entitlements_raw": {k: _plain(v) for k, v in ents.items()},
           "entitlements_analysis": analysis}
    (EVIDENCE / "storage_entitlements.json").write_text(json.dumps(out, indent=1))
    write_md(EVIDENCE / "storage_entitlements.md", storage, analysis)
    print(f"\nwrote {EVIDENCE/'storage_entitlements.md'}")


def _plain(v):
    if isinstance(v, (bytes, bytearray)):
        return f"<{len(v)} bytes>"
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def write_md(path, storage, analysis):
    L = ["# Storage and entitlements (Phase 2.5, 2.6)\n",
         "Generated by `scripts/02_storage_entitlements.py`.\n"]

    L.append("\n## Directory APIs each binary calls\n")
    L.append("An imported symbol is hard evidence the binary asks for that location.\n")
    L.append("\n| Binary | directory APIs imported | path-ish strings |")
    L.append("|---|---|---|")
    for r, d in sorted(storage.items()):
        apis = ", ".join(f"`{k}`" for k in sorted(d["directory_apis_imported"])) or "—"
        L.append(f"| `{r}` | {apis} | {d['path_strings_total']} |")

    L.append("\n## Entitlements against a free Apple developer account\n")
    for binary, rows in sorted(analysis.items()):
        L.append(f"\n### `{binary}`\n")
        L.append("| Entitlement | Value | Free account | Feature lost |")
        L.append("|---|---|---|---|")
        for row in rows:
            v = row["value"]
            vs = str(v)
            if len(vs) > 60:
                vs = vs[:57] + "..."
            L.append(f"| `{row['entitlement']}` | `{vs}` | {row['free_account']} | "
                     f"{row['feature']} |")
    path.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
