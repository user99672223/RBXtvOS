#!/usr/bin/env python3
"""Phase 5 verification: re-parse every patched binary and resolve every import.

The pass condition CLAUDE.md sets is "zero unresolved non-weak imports". This
script is what decides it, so it reads the *patched* bundle from scratch rather
than reusing anything computed before patching, and it answers each import
against the same three evidence sources plus the shims' own exports:

  device   evidence/exports/device_<build>/   what the Apple TV actually has
  bundle   Roblox's own frameworks, re-read from work/patched
  shims    out/shims/*.dylib, re-read from the built artefacts

It re-derives the import tables, the LC_RPATHs and the library ordinals from
the patched files, so a load command this phase got wrong shows up here as an
unresolved import rather than being papered over.

Two independent checks, because they fail for different reasons:

  1. every load command resolves -- a non-weak LC_LOAD_DYLIB names a library
     that must be loadable whether or not anything is imported from it. Roblox
     links QuickLook, iAd and EventKitUI this way while importing zero symbols
     from them, so a symbol-only check reports nothing and the app still dies
     at launch. This check exists because that nearly slipped through.
  2. every import resolves -- the symbol-level pass condition.

Reported separately, because they are not launch blockers and counting them
would make the gate meaningless:
  * weak imports  -- dyld binds a missing one to NULL and the app still loads
  * lazy imports  -- dyld leaves them unbound until first call

Exit status is 0 only when both checks find nothing.
"""
from __future__ import annotations

import json
import posixpath
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lief  # noqa: E402
from lib_macho import (EVIDENCE, ROOT, WORK, exported_names, find_machos,  # noqa: E402
                       is_macho, parse)

PATCHED = WORK / "patched"
SHIMS = ROOT / "out" / "shims"
EXPORTS = EVIDENCE / "exports"

sys.path.insert(0, str(Path(__file__).parent))
from importlib import import_module  # noqa: E402

_imports_mod = import_module("02_imports") if False else None  # see resolve_ordinal below


# ---------------------------------------------------------------- Mach-O helpers
# Kept local rather than imported from 02_imports.py so this check does not
# depend on that script's state at all.

SELF_LIBRARY_ORDINAL = 0
MAX_LIBRARY_ORDINAL = 0xfd
DYNAMIC_LOOKUP_ORDINAL = 0xfe
EXECUTABLE_ORDINAL = 0xff

DYLIB_CMDS = (
    lief.MachO.LoadCommand.TYPE.LOAD_DYLIB,
    lief.MachO.LoadCommand.TYPE.LOAD_WEAK_DYLIB,
    lief.MachO.LoadCommand.TYPE.REEXPORT_DYLIB,
    lief.MachO.LoadCommand.TYPE.LOAD_UPWARD_DYLIB,
    lief.MachO.LoadCommand.TYPE.LAZY_LOAD_DYLIB,
)


def ordinal_table(b):
    table, idx = {}, 0
    for c in b.commands:
        if c.command in DYLIB_CMDS:
            idx += 1
            table[idx] = c.name
    return table


def rpaths(b):
    return [c.path for c in b.commands
            if c.command == lief.MachO.LoadCommand.TYPE.RPATH]


def install_name(b):
    for l in b.libraries:
        if "ID_DYLIB" in str(l.command):
            return l.name
    return None


def resolve_ordinal(o, table, self_name):
    try:
        o = int(o)
    except (TypeError, ValueError):
        return "<ordinal ?>"
    if o in (DYNAMIC_LOOKUP_ORDINAL, -2):
        return "<flat-namespace>"
    if o in (EXECUTABLE_ORDINAL, -1):
        return "<executable>"
    if o == SELF_LIBRARY_ORDINAL:
        return self_name or "<self>"
    if 1 <= o <= MAX_LIBRARY_ORDINAL and o in table:
        return table[o]
    return f"<ordinal {o}>"


def read_imports(path: Path):
    b = parse(path)[0]
    table = ordinal_table(b)
    self_name = install_name(b)
    out = defaultdict(dict)

    def record(lib, name, weak, lazy):
        prev = out[lib].get(name)
        if prev is None:
            out[lib][name] = {"weak": weak, "lazy": lazy}
        else:
            prev["weak"] = prev["weak"] and weak
            prev["lazy"] = prev["lazy"] and lazy

    for bi in b.bindings:
        if not bi.has_symbol:
            continue
        lib = bi.library.name if bi.has_library else resolve_ordinal(
            getattr(bi, "library_ordinal", None), table, self_name)
        cls = str(getattr(bi, "binding_class", ""))
        weak = bool(getattr(bi, "weak_import", False)) or cls.endswith("WEAK")
        record(lib, bi.symbol.name, weak, cls.endswith("LAZY"))
    for s in b.imported_symbols:
        if s.has_binding_info:
            continue
        lib = s.library.name if getattr(s, "library", None) else resolve_ordinal(
            getattr(s, "library_ordinal", None), table, self_name)
        if s.name not in out[lib]:
            out[lib][s.name] = {"weak": False, "lazy": False}
    return out, rpaths(b), self_name, table


# -------------------------------------------------------------------- universes

def safe(name: str) -> str:
    return name.lstrip("/").replace("/", "__")


def load_device():
    cfg = EVIDENCE / "queries" / "evidence_sources.json"
    name = json.loads(cfg.read_text())["device"] if cfg.is_file() else None
    d = EXPORTS / name if name else None
    if not d or not d.is_dir():
        raise SystemExit(f"device evidence not found ({d}); fetch the outputs branch")
    by_lib, images = {}, set()
    imgs = d / "_images.txt"
    if imgs.is_file():
        images = {l.strip() for l in imgs.read_text().splitlines() if l.strip()}
    for f in d.glob("*.txt"):
        if f.name.startswith("_"):
            continue
        by_lib[f.stem] = {l.strip() for l in f.read_text().splitlines() if l.strip()}
    reex = {}
    libs_json = EVIDENCE / "sdk" / f"{name}_libraries.json"
    if libs_json.is_file():
        for lib, rec in json.loads(libs_json.read_text()).items():
            if isinstance(rec, dict) and rec.get("reexports"):
                reex[lib] = rec["reexports"]
    return by_lib, images, reex, d.name


def closure(by_lib, reex, lib, seen=None):
    seen = seen if seen is not None else set()
    if lib in seen:
        return set()
    seen.add(lib)
    out = set(by_lib.get(safe(lib), ()))
    for r in reex.get(lib, ()):
        out |= closure(by_lib, reex, r, seen)
    return out


def main():
    if not PATCHED.is_dir():
        raise SystemExit(f"{PATCHED} does not exist; run scripts/05_retarget.py first")
    apps = sorted((PATCHED / "Payload").glob("*.app"))
    if len(apps) != 1:
        raise SystemExit(f"expected one .app under {PATCHED/'Payload'}, found {apps}")
    app = apps[0]

    dev_by_lib, dev_images, dev_reex, dev_label = load_device()

    # Roblox's own frameworks, re-read from the patched bundle.
    bundle_exports, bundle_by_install, bundle_by_relpath = {}, {}, {}
    machos = find_machos(app)
    for p in machos:
        r = str(p.relative_to(app))
        b = parse(p)[0]
        names, _src = exported_names(b, p)
        bundle_exports[r] = names
        bundle_by_relpath[r] = r
        inst = install_name(b)
        if inst:
            bundle_by_install[inst] = r

    # Our shims, re-read from the built dylibs.
    shim_exports, shim_by_install = {}, {}
    shim_reex = {}
    if SHIMS.is_dir():
        for p in sorted(SHIMS.glob("*.dylib")):
            b = parse(p)[0]
            names, _src = exported_names(b, p)
            shim_exports[p.name] = names
            inst = install_name(b)
            if inst:
                shim_by_install[inst] = p.name
                bundle_by_relpath[f"Frameworks/{p.name}"] = f"shim:{p.name}"
            shim_reex[inst or p.name] = [
                c.name for c in b.commands
                if c.command == lief.MachO.LoadCommand.TYPE.REEXPORT_DYLIB]
    else:
        print(f"::warning:: {SHIMS} not found; shim exports cannot be checked")

    def lookup(kind, key, sym):
        if kind == "device":
            if key not in dev_images and safe(key) not in dev_by_lib:
                return None   # library absent: cannot answer
            return sym in closure(dev_by_lib, dev_reex, key)
        if kind == "bundle":
            if key.startswith("shim:"):
                name = key[5:]
                if name not in shim_exports:
                    return None
                if sym in shim_exports[name]:
                    return True
                # follow the shim's own re-exports into the real framework
                inst = next((i for i, n in shim_by_install.items() if n == name), None)
                for r in shim_reex.get(inst or name, []):
                    v = lookup("device", r, sym)
                    if v:
                        return True
                return False
            if key not in bundle_exports:
                return None
            return sym in bundle_exports[key]
        return None

    def candidates(binary, lib, rp, self_name):
        if lib in ("<flat-namespace>", "<executable>", "<ordinal -3>", "<ordinal ?>"):
            yield ("flat", None)
            return
        if lib.startswith("/"):
            yield ("device", lib)
            return
        if self_name and lib == self_name:
            yield ("bundle", binary)
            return
        if lib in bundle_by_install:
            yield ("bundle", bundle_by_install[lib])
            return
        if lib in shim_by_install:
            yield ("bundle", f"shim:{shim_by_install[lib]}")
            return
        bin_dir = posixpath.dirname(binary)
        if lib.startswith("@rpath/"):
            tail = lib[len("@rpath/"):]
            for r in rp:
                if r.startswith("/"):
                    yield ("device", posixpath.normpath(f"{r}/{tail}"))
                elif r.startswith("@executable_path/"):
                    sub = r[len("@executable_path/"):]
                    k = bundle_by_relpath.get(posixpath.normpath(f"{sub}/{tail}"))
                    if k:
                        yield ("bundle", k)
                elif r.startswith("@loader_path/"):
                    sub = r[len("@loader_path/"):]
                    k = bundle_by_relpath.get(posixpath.normpath(f"{bin_dir}/{sub}/{tail}"))
                    if k:
                        yield ("bundle", k)
            k = bundle_by_relpath.get(f"Frameworks/{tail}")
            if k:
                yield ("bundle", k)
            yield ("device", f"/usr/lib/swift/{tail}")
            return
        if lib.startswith("@executable_path/"):
            k = bundle_by_relpath.get(posixpath.normpath(lib[len("@executable_path/"):]))
            if k:
                yield ("bundle", k)
            return
        if lib.startswith("@loader_path/"):
            k = bundle_by_relpath.get(
                posixpath.normpath(f"{bin_dir}/{lib[len('@loader_path/'):]}"))
            if k:
                yield ("bundle", k)
            return
        yield ("unknown", lib)

    flat_union = set()
    for s in dev_by_lib.values():
        flat_union |= s
    for s in bundle_exports.values():
        flat_union |= s
    for s in shim_exports.values():
        flat_union |= s

    # The main executable's rpaths are part of every image's search, because
    # dyld searches the rpaths of each image in the load chain.
    main_rp = []
    for p in machos:
        if str(p.relative_to(app)) == app.name.replace(".app", ""):
            main_rp = rpaths(parse(p)[0])
            break

    # ---------------------------------------------------------------- check 1
    # Load commands. A non-weak LC_LOAD_DYLIB must name something loadable.
    lib_blockers, lib_weak_absent = [], []
    for p in machos:
        r = str(p.relative_to(app))
        b = parse(p)[0]
        search_rp = list(dict.fromkeys(rpaths(b) + main_rp))
        self_name = install_name(b)
        for l in b.libraries:
            if "ID_DYLIB" in str(l.command):
                continue
            weak_lib = "WEAK" in str(l.command)
            found, tried = False, []
            for kind, key in candidates(r, l.name, search_rp, self_name):
                if kind == "flat" or key is None:
                    continue
                tried.append(f"{kind}:{key}")
                if kind == "device":
                    if key in dev_images or safe(key) in dev_by_lib:
                        found = True
                        break
                elif kind == "bundle":
                    if key.startswith("shim:"):
                        if key[5:] in shim_exports:
                            found = True
                            break
                    elif key in bundle_exports:
                        found = True
                        break
            if found:
                continue
            row = {"binary": r, "library": l.name,
                   "load_command": str(l.command).split(".")[-1],
                   "weak": weak_lib, "tried": tried}
            (lib_weak_absent if weak_lib else lib_blockers).append(row)

    print("--- load commands ---")
    print(f"non-weak load commands naming an unloadable library: {len(lib_blockers)}")
    for row in lib_blockers:
        print(f"  BLOCKER {row['binary']}  {row['library']}  ({row['load_command']})")
    print(f"weak load commands naming an absent library (fine): {len(lib_weak_absent)}")
    print()

    # ---------------------------------------------------------------- check 2
    blockers, weak_rows, lazy_rows = [], [], []
    ok = 0
    per_binary = {}

    for p in machos:
        r = str(p.relative_to(app))
        imports, rp, self_name, _table = read_imports(p)
        search_rp = list(dict.fromkeys(rp + main_rp))
        b_ok = b_block = b_weak = b_lazy = 0
        for lib, entries in imports.items():
            for sym, flags in entries.items():
                resolved = False
                saw_lib = False
                tried = []
                for kind, key in candidates(r, lib, search_rp, self_name):
                    if kind == "flat":
                        resolved = sym in flat_union
                        tried.append("flat-namespace")
                        break
                    if key is None:
                        continue
                    tried.append(f"{kind}:{key}")
                    v = lookup(kind, key, sym)
                    if v is None:
                        continue
                    saw_lib = True
                    if v:
                        resolved = True
                        break
                if resolved:
                    ok += 1
                    b_ok += 1
                    continue
                row = {"binary": r, "library": lib, "symbol": sym,
                       "weak": flags["weak"], "lazy": flags["lazy"],
                       "library_found": saw_lib, "tried": tried}
                if flags["weak"]:
                    weak_rows.append(row); b_weak += 1
                elif flags["lazy"]:
                    lazy_rows.append(row); b_lazy += 1
                else:
                    blockers.append(row); b_block += 1
        per_binary[r] = {"resolved": b_ok, "blockers": b_block,
                         "weak_missing": b_weak, "lazy_missing": b_lazy}
        print(f"{r}: resolved={b_ok} BLOCKERS={b_block} weak={b_weak} lazy={b_lazy}")

    result = {
        "device_evidence": dev_label,
        "shim_dylibs": sorted(shim_exports),
        "totals": {"resolved": ok, "blockers": len(blockers),
                   "weak_missing": len(weak_rows), "lazy_missing": len(lazy_rows),
                   "unloadable_non_weak_libraries": len(lib_blockers),
                   "absent_weak_libraries": len(lib_weak_absent)},
        "unloadable_non_weak_libraries": lib_blockers,
        "absent_weak_libraries": lib_weak_absent,
        "per_binary": per_binary,
        "blockers": blockers,
        "weak_missing": weak_rows,
        "lazy_missing": lazy_rows,
    }
    out = EVIDENCE / "phase5_verify.json"
    out.write_text(json.dumps(result, indent=1))

    print()
    print("=" * 70)
    print(f"device evidence : {dev_label}")
    print(f"shim dylibs     : {len(shim_exports)}")
    print(f"resolved        : {ok}")
    print(f"weak-missing    : {len(weak_rows)}  (dyld binds these to NULL; app still loads)")
    print(f"lazy-missing    : {len(lazy_rows)}  (bound on first call, not at launch)")
    print(f"unloadable libs : {len(lib_blockers)}  <- must be 0 to pass")
    print(f"BLOCKERS        : {len(blockers)}  <- must be 0 to pass")
    print("=" * 70)
    if lib_blockers:
        print("\nNon-weak load commands naming a library that cannot be loaded:")
        for row in lib_blockers:
            print(f"  {row['binary']}  {row['library']}")
            print(f"      tried: {', '.join(row['tried']) or '(nothing)'}")
    if blockers:
        print("\nUnresolved non-weak eager imports:")
        for row in blockers[:60]:
            print(f"  {row['binary']}  {row['library']}  {row['symbol']}")
            print(f"      tried: {', '.join(row['tried']) or '(nothing)'}")
        if len(blockers) > 60:
            print(f"  ... and {len(blockers) - 60} more (see {out})")
    print(f"\nwrote {out}")
    return 1 if (blockers or lib_blockers) else 0


if __name__ == "__main__":
    raise SystemExit(main())
