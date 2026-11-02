#!/usr/bin/env python3
"""Synthesise a .tbd stub for a framework the tvOS SDK does not ship.

Why this is needed
------------------
Two of the Phase 4 proxies stand in front of frameworks that are *private on
tvOS*: CoreTelephony and PushKit both exist in the device's dyld shared cache
(5,167 and 41 exported names respectively) but neither has a .tbd in tvOS SDK
27.2. A proxy has to re-export the real framework, and ld needs something to
read in order to do that -- it failed with

    ld: library '.../AppleTVOS27.2.sdk/System/Library/Frameworks/
        CoreTelephony.framework/CoreTelephony.tbd' not found

This builds the missing stub from evidence source 1, the device's own export
list, which is exactly the authority on what that library offers on this
device. The stub carries the real install name, so at runtime dyld's re-export
resolves into the framework on the device, not into anything we ship.

This is also the concrete cost of a symbol being private on tvOS, which
CLAUDE.md asks to be flagged separately: there is no public contract, so the
only description of the library available is the one the device itself gives.

usage: make_tbd.py <install-name> <device-export-list.txt> <out.tbd> [targets]
"""
from __future__ import annotations

import sys
from pathlib import Path

CLASS_PREFIX = "_OBJC_CLASS_$_"
METACLASS_PREFIX = "_OBJC_METACLASS_$_"
IVAR_PREFIX = "_OBJC_IVAR_$_"
EHTYPE_PREFIX = "_OBJC_EHTYPE_$_"


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    install_name, listing, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    targets = sys.argv[4] if len(sys.argv) > 4 else "arm64-tvos, arm64e-tvos"

    if not listing.is_file():
        print(f"::error::device export list not found: {listing}")
        return 1

    symbols, classes, ivars, ehtypes = [], [], [], []
    for raw in listing.read_text().splitlines():
        name = raw.strip()
        if not name:
            continue
        if name.startswith(CLASS_PREFIX):
            classes.append(name[len(CLASS_PREFIX):])
        elif name.startswith(METACLASS_PREFIX):
            # a metaclass comes with its class in a .tbd's objc-classes entry
            cls = name[len(METACLASS_PREFIX):]
            if cls not in classes:
                classes.append(cls)
        elif name.startswith(IVAR_PREFIX):
            ivars.append(name[len(IVAR_PREFIX):])
        elif name.startswith(EHTYPE_PREFIX):
            ehtypes.append(name[len(EHTYPE_PREFIX):])
        else:
            symbols.append(name)

    def block(key, values):
        if not values:
            return ""
        # single-quote every entry: Swift and ObjC names contain $, and YAML
        # would otherwise take some of them as anchors or aliases
        items = ", ".join("'" + v.replace("'", "''") + "'" for v in sorted(set(values)))
        return f"    {key}:{' ' * max(1, 18 - len(key))}[ {items} ]\n"

    text = (
        "--- !tapi-tbd\n"
        "tbd-version:     4\n"
        f"targets:         [ {targets} ]\n"
        f"install-name:    '{install_name}'\n"
        "exports:\n"
        f"  - targets:         [ {targets} ]\n"
        + block("symbols", symbols)
        + block("objc-classes", classes)
        + block("objc-ivars", ivars)
        + block("objc-eh-types", ehtypes)
        + "...\n"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"wrote {out}: {len(symbols)} symbols, {len(classes)} classes, "
          f"{len(ivars)} ivars, {len(ehtypes)} eh-types  (install-name {install_name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
