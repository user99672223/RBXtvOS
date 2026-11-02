#!/usr/bin/env python3
"""Phase 4 check: does each shim export exactly what it was built to supply?

The Phase 5 verifier already proves Roblox's imports resolve, but it proves it
transitively: a symbol could resolve through a proxy's re-export into the real
framework while the symbol the shim was supposed to add is quietly absent. That
would look fine until the moment Roblox touched it.

So this checks the contract directly, per shim, and derives the expectation
from the evidence rather than a hardcoded list, so it stays correct when Roblox
updates:

    evidence/missing.json      which symbols each library was missing
    evidence/queries/shim_map.json  which shim stands in for that library
    out/shims/*.dylib          what each shim actually exports
    work/patched/              which binaries still exist after Phase 5

The last one matters. missing.json is computed over the *unpacked* bundle, so
it still lists the 46 WidgetKit symbols that only the widget extension imported
-- and that extension is removed in Phase 5. A symbol counts as required here
only if at least one binary that still imports it survives into the patched
bundle; otherwise this would demand that the WidgetKit stand-in implement the
whole timeline-provider API for an extension that is no longer shipped.

Also checks the structural promises the shims rely on:
  * every dylib targets tvOS, or it will not load on the device at all
  * its install name is @rpath/..., matching what Phase 5 repoints to
  * a proxy carries LC_REEXPORT_DYLIB -- without it, every *other* symbol
    Roblox imports from that library would stop resolving
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lief  # noqa: E402
from lib_macho import (EVIDENCE, ROOT, WORK, exported_names,  # noqa: E402
                       find_machos, parse)

SHIMS = ROOT / "out" / "shims"
PATCHED = WORK / "patched"

# Libraries the shim stands in *front* of rather than replacing: it must
# re-export the real one so the rest of its symbols keep resolving.
PROXIES = {
    "/System/Library/Frameworks/UIKit.framework/UIKit",
    "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
    "/System/Library/Frameworks/CoreTelephony.framework/CoreTelephony",
    "/System/Library/Frameworks/PushKit.framework/PushKit",
    "/usr/lib/swift/libswiftDarwin.dylib",
}


def main():
    if not SHIMS.is_dir():
        raise SystemExit(f"{SHIMS} not found; build the shims and fetch them with "
                         f"scripts/fetch_outputs.sh")
    missing = json.loads((EVIDENCE / "missing.json").read_text())

    # Binaries that survive Phase 5. A requirement whose only importers were
    # removed with an app extension is not a requirement any more.
    surviving = None
    apps = sorted((PATCHED / "Payload").glob("*.app")) if (PATCHED / "Payload").is_dir() else []
    if apps:
        surviving = {str(p.relative_to(apps[0])) for p in find_machos(apps[0])}
        print(f"patched bundle has {len(surviving)} Mach-Os; "
              f"requirements are scoped to those\n")
    else:
        print("::warning:: no patched bundle found; checking against every "
              "requirement in missing.json, including ones belonging to removed "
              "app extensions\n")
    shim_map = {k: v for k, v in
                json.loads((EVIDENCE / "queries" / "shim_map.json").read_text()).items()
                if not k.startswith("_")}

    problems = []
    rows = []

    for lib, shim_install in sorted(shim_map.items()):
        name = shim_install.rsplit("/", 1)[-1]
        path = SHIMS / name
        if not path.is_file():
            problems.append(f"{name}: not built, but shim_map points {lib} at it")
            continue
        b = parse(path)[0]
        exports, _src = exported_names(b, path)

        # structural promises
        bv = None
        for c in b.commands:
            if c.command == lief.MachO.LoadCommand.TYPE.BUILD_VERSION:
                bv = int(c.platform)
        if bv != 3:  # PLATFORM_TVOS
            problems.append(f"{name}: LC_BUILD_VERSION platform is {bv}, expected 3 (tvOS)")
        install = next((l.name for l in b.libraries if "ID_DYLIB" in str(l.command)), None)
        if install != shim_install:
            problems.append(f"{name}: install name is {install!r}, "
                            f"but Phase 5 repoints load commands to {shim_install!r}")
        reexports = [c.name for c in b.commands
                     if c.command == lief.MachO.LoadCommand.TYPE.REEXPORT_DYLIB]
        if lib in PROXIES:
            if not reexports:
                problems.append(f"{name}: proxy for {lib} has no LC_REEXPORT_DYLIB; "
                                f"every other symbol Roblox imports from that library "
                                f"would stop resolving")
            elif lib not in reexports:
                problems.append(f"{name}: proxy re-exports {reexports}, not {lib}")

        # the symbols it was built to supply, scoped to binaries that remain
        entries = missing["missing_symbols"].get(lib, {})
        if surviving is None:
            required = sorted(entries)
        else:
            required = sorted(s for s, importers in entries.items()
                              if any(b in surviving for b in importers))
        absent = [s for s in required if s not in exports]
        for s in absent:
            problems.append(f"{name}: does not export {s}, which {lib} was missing")

        rows.append((name, lib in PROXIES, len(required) - len(absent), len(required),
                     len(exports), len(reexports)))

    print(f"{'shim':46} {'kind':8} {'supplied':>10} {'exports':>8} {'reexp':>6}")
    for name, is_proxy, got, want, nexp, nreex in rows:
        print(f"{name:46} {'proxy' if is_proxy else 'stand-in':8} "
              f"{got}/{want:<8} {nexp:8} {nreex:6}")

    print()
    if problems:
        print(f"{len(problems)} problem(s):")
        for p in problems:
            print(f"  ::error:: {p}")
        return 1
    print(f"all {len(rows)} shims export what they were built to supply, "
          f"target tvOS, and carry the right install name and re-exports")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
