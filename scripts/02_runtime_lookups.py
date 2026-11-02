#!/usr/bin/env python3
"""Phase 2.4: names Roblox may resolve dynamically, not through the linker.

A shim list built only from import tables misses anything the app looks up by
string at runtime: `NSClassFromString`, `objc_getClass`, `dlopen`, `dlsym`,
`NSSelectorFromString`, `+[NSBundle bundleWithPath:]`. Those calls fail softly
on tvOS (nil class, NULL handle) rather than failing to load, so they will not
show up in the Phase 5 import-resolution check and have to be found here.

What this does, with no disassembly (RobloxLib alone is 107 MB):

  1. Records which binaries import the dynamic-lookup functions at all. If a
     binary imports none of them, it cannot do a string lookup, and that is a
     hard negative worth recording.
  2. Collects every C string in each binary and keeps the ones that could name
     something loadable: ObjC-class-shaped identifiers, framework and dylib
     paths, and bare framework names.
  3. Cross-checks each candidate against the device's own class and image
     lists when device evidence is available (evidence/exports/device_*), so
     the output says present / absent on the device rather than guessing.

Candidates that cannot be checked yet are written out for the device-evidence
workflow to resolve, and stay UNVERIFIED in the report until it does.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_macho import EVIDENCE, app_dir, find_machos, parse, rel  # noqa: E402

# Functions that turn a string into code or a class at runtime.
DYNAMIC = {
    "_NSClassFromString": "class by name",
    "_objc_getClass": "class by name",
    "_objc_lookUpClass": "class by name",
    "_objc_getRequiredClass": "class by name (aborts if absent)",
    "_objc_getMetaClass": "metaclass by name",
    "_NSSelectorFromString": "selector by name",
    "_sel_getUid": "selector by name",
    "_sel_registerName": "selector by name",
    "_NSProtocolFromString": "protocol by name",
    "_objc_getProtocol": "protocol by name",
    "_dlopen": "load a library by path",
    "_dlsym": "symbol by name",
    "_dlopen_preflight": "probe a library by path",
    "_CFBundleCreate": "bundle by URL",
    "_CFBundleGetBundleWithIdentifier": "bundle by identifier",
}

# A plausible ObjC class name: CamelCase, often a 2-3 letter prefix.
CLASS_RE = re.compile(r"^[A-Z][A-Za-z0-9_]{2,59}$")
# Paths that name something loadable.
PATH_RE = re.compile(
    r"^(?:/[A-Za-z0-9_.+-]+)*/(?:[A-Za-z0-9_.+-]+\.framework/[A-Za-z0-9_.+-]+|"
    r"[A-Za-z0-9_.+-]+\.(?:dylib|bundle|appex))$")
DYLIB_RE = re.compile(r"^(?:lib)?[A-Za-z0-9_.+-]+\.dylib$")
FRAMEWORK_NAME_RE = re.compile(r"^[A-Za-z0-9_+-]+\.framework$")

# Classes whose absence on tvOS would matter; prefixes Apple uses for the
# frameworks Roblox links. Used only to rank, never to decide.
APPLE_PREFIXES = ("NS", "UI", "CA", "CB", "CL", "CM", "AV", "MP", "SK", "GC",
                  "GK", "MF", "MK", "EK", "PH", "WK", "SF", "AS", "LA", "CN",
                  "CT", "PK", "QL", "UN", "MX", "WL", "AD", "MA", "DAR", "_UI")

SKIP_SEGMENTS = {"__PAGEZERO"}


def cstrings(b):
    """Every NUL-terminated string in the string-ish sections of a Mach-O."""
    out = set()
    for s in b.sections:
        if s.segment_name in SKIP_SEGMENTS:
            continue
        if s.name not in ("__cstring", "__objc_classname", "__objc_methname",
                          "__objc_methtype", "__const", "__cfstring",
                          "__oslogstring", "__ustring", "__data"):
            continue
        try:
            data = bytes(s.content)
        except Exception:
            continue
        for raw in data.split(b"\0"):
            if 3 <= len(raw) <= 300:
                try:
                    out.add(raw.decode("utf-8"))
                except UnicodeDecodeError:
                    continue
    return out


def load_device_names():
    """Classes and images the device cache is known to have, if evidence exists."""
    classes, images = set(), set()
    exports = EVIDENCE / "exports"
    if not exports.is_dir():
        return classes, images, []
    sources = []
    for d in sorted(exports.glob("device_*")):
        sources.append(d.name)
        imgs = d / "_images.txt"
        if imgs.is_file():
            images.update(l.strip() for l in imgs.read_text().splitlines() if l.strip())
        for f in d.glob("*.txt"):
            if f.name == "_images.txt":
                continue
            for line in f.read_text().splitlines():
                line = line.strip()
                if line.startswith("_OBJC_CLASS_$_"):
                    classes.add(line[len("_OBJC_CLASS_$_"):])
    return classes, images, sources


def main():
    app = app_dir()
    out = EVIDENCE
    dev_classes, dev_images, dev_sources = load_device_names()

    per_binary = {}
    all_candidates = {"classes": set(), "paths": set(), "dylibs": set(),
                      "frameworks": set()}

    for p in find_machos(app):
        r = rel(p, app)
        b = parse(p)[0]
        imported = {s.name for s in b.imported_symbols}
        dyn = {k: v for k, v in DYNAMIC.items() if k in imported}
        strings = cstrings(b)

        cls = {s for s in strings if CLASS_RE.match(s)}
        paths = {s for s in strings if PATH_RE.match(s)}
        dylibs = {s for s in strings if DYLIB_RE.match(s)}
        fw = {s for s in strings if FRAMEWORK_NAME_RE.match(s)}

        # Rank class candidates: Apple-prefixed ones are the ones that can be
        # absent on tvOS; Roblox's own classes (RB*, Rbx*) always ship with it.
        apple_cls = {c for c in cls if c.startswith(APPLE_PREFIXES)}

        per_binary[r] = {
            "dynamic_lookup_imports": dyn,
            "can_do_string_lookup": bool(dyn),
            "total_strings": len(strings),
            "class_shaped_strings": len(cls),
            "apple_prefixed_class_candidates": sorted(apple_cls),
            "loadable_paths": sorted(paths),
            "dylib_names": sorted(dylibs),
            "framework_names": sorted(fw),
        }
        all_candidates["classes"] |= apple_cls
        all_candidates["paths"] |= paths
        all_candidates["dylibs"] |= dylibs
        all_candidates["frameworks"] |= fw
        print(f"{r}: dynamic={sorted(dyn)} apple-class-candidates={len(apple_cls)} "
              f"paths={len(paths)} dylibs={len(dylibs)}")

    cand = {k: sorted(v) for k, v in all_candidates.items()}
    checked = {}
    if dev_classes:
        for c in cand["classes"]:
            checked[c] = "present" if c in dev_classes else "ABSENT-or-unqueried"
    (out / "queries").mkdir(parents=True, exist_ok=True)
    (out / "queries" / "runtime_lookup_candidates.json").write_text(
        json.dumps({"classes": cand["classes"], "paths": cand["paths"],
                    "dylibs": cand["dylibs"], "frameworks": cand["frameworks"]},
                   indent=1))
    (out / "runtime_lookups.json").write_text(json.dumps(
        {"per_binary": per_binary, "candidates": cand,
         "device_check": checked, "device_evidence_sources": dev_sources}, indent=1))

    write_markdown(out / "runtime_lookups.md", per_binary, cand, checked, dev_sources)
    print(f"\ncandidates: {len(cand['classes'])} Apple-prefixed classes, "
          f"{len(cand['paths'])} paths, {len(cand['dylibs'])} dylib names, "
          f"{len(cand['frameworks'])} framework names")
    if not dev_sources:
        print("no device evidence yet: candidates stay UNVERIFIED")


def write_markdown(path, per_binary, cand, checked, dev_sources):
    L = []
    L.append("# Runtime (non-linker) lookups\n")
    L.append("Generated by `scripts/02_runtime_lookups.py`. Covers names Roblox can resolve\n"
             "by string at runtime, which the Phase 5 import-resolution check cannot see\n"
             "because nothing in the import tables refers to them.\n")
    if dev_sources:
        L.append(f"Device evidence used: {', '.join(dev_sources)}\n")
    else:
        L.append("**No device evidence present yet — every candidate below is UNVERIFIED.**\n")

    L.append("\n## Which binaries can look something up by string\n")
    L.append("| Binary | dynamic-lookup imports | verdict |")
    L.append("|---|---|---|")
    for r, d in sorted(per_binary.items()):
        imports = ", ".join(f"`{k}`" for k in sorted(d["dynamic_lookup_imports"])) or "—"
        verdict = "can do string lookups" if d["can_do_string_lookup"] else "**cannot** (no such import)"
        L.append(f"| `{r}` | {imports} | {verdict} |")

    L.append("\n## Apple-prefixed class-name candidates\n")
    L.append("String constants shaped like Apple class names. Presence of the string is not\n"
             "proof it is passed to `NSClassFromString`; it bounds the set to check.\n")
    if checked:
        absent = [c for c, v in sorted(checked.items()) if v != "present"]
        present = [c for c, v in sorted(checked.items()) if v == "present"]
        L.append(f"- checked against the device cache: {len(present)} present, "
                 f"{len(absent)} not found in the queried export closure\n")
        if absent:
            L.append("\nNot found (each needs a direct cache check before it is called missing):\n")
            for c in absent:
                L.append(f"- `{c}`")
    else:
        for c in cand["classes"]:
            L.append(f"- `{c}` — UNVERIFIED")

    L.append("\n## Loadable paths referenced as strings\n")
    if cand["paths"]:
        for s in cand["paths"]:
            L.append(f"- `{s}`")
    else:
        L.append("None.\n")

    L.append("\n## Bare dylib names referenced as strings\n")
    if cand["dylibs"]:
        for s in cand["dylibs"]:
            L.append(f"- `{s}`")
    else:
        L.append("None.\n")

    L.append("\n## Bare framework names referenced as strings\n")
    if cand["frameworks"]:
        for s in cand["frameworks"]:
            L.append(f"- `{s}`")
    else:
        L.append("None.\n")

    path.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
