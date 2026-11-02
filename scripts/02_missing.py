#!/usr/bin/env python3
"""Phase 2.3: compute evidence/missing.json, the shim list.

Resolution model
----------------
Roblox's Mach-Os are two-level-namespace bound: every import names both a
symbol and the library it must come from, so an import resolves if and only if
that library exports the symbol, or re-exports another library that does. This
mirrors what dyld does at load time, which is what the Phase 5 pass condition
("zero unresolved non-weak imports") actually tests.

Each import is classified against three export universes:

  device  evidence/exports/device_<build>/   source 1, the authority on what
                                             exists on the Apple TV
  tvossdk evidence/exports/sdk_tvos<ver>/    source 2, the public contract
  iossdk  evidence/exports/sdk_ios<ver>/     source 3, used only to confirm a
                                             missing item really exists on iOS,
                                             which rules out a parsing error
  bundle  evidence/exports/bundle/           Roblox's own frameworks, for
                                             @rpath imports

Outcomes per import:

  ok                  resolves on the device
  MISSING_FRAMEWORK   the library itself is absent from the device
  MISSING_SYMBOL      the library is present but does not export the symbol
  weak-missing        missing but weakly imported, so dyld binds it to NULL and
                      the app still loads; recorded, never shimmed
  unresolvable-source no export evidence for that library yet (UNVERIFIED)

Separately flagged: symbols that the device exports but the tvOS SDK does not.
Those link and run but have no public contract, so any shim that has to
interoperate with them is building on private behaviour.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_macho import EVIDENCE  # noqa: E402

IMPORTS = EVIDENCE / "imports"
EXPORTS = EVIDENCE / "exports"
SDK = EVIDENCE / "sdk"

CLASS_PREFIX = "_OBJC_CLASS_$_"
METACLASS_PREFIX = "_OBJC_METACLASS_$_"
IVAR_PREFIX = "_OBJC_IVAR_$_"


def safe(name: str) -> str:
    return name.lstrip("/").replace("/", "__")


class Universe:
    """Export lists for one evidence source, keyed by library install name."""

    def __init__(self, directory: Path, name: str, reexports: dict | None = None):
        self.dir = directory
        self.name = name
        self.reexports = reexports or {}
        self.by_lib: dict[str, set] = {}
        self.images: set[str] | None = None
        if not directory.is_dir():
            return
        imgs = directory / "_images.txt"
        if imgs.is_file():
            self.images = {l.strip() for l in imgs.read_text().splitlines() if l.strip()}
        for f in sorted(directory.glob("*.txt")):
            if f.name.startswith("_"):
                continue
            key = f.stem
            self.by_lib[key] = {l.strip() for l in f.read_text().splitlines() if l.strip()}

    def present(self) -> bool:
        return bool(self.by_lib) or bool(self.images)

    def has_library(self, lib: str):
        """True/False if known, None if this source cannot say."""
        if self.images is not None:
            return lib in self.images
        if safe(lib) in self.by_lib:
            return True
        return None if not self.by_lib else False

    def closure(self, lib: str, _seen=None) -> set:
        """Exports of lib plus, transitively, everything it re-exports."""
        seen = _seen if _seen is not None else set()
        if lib in seen:
            return set()
        seen.add(lib)
        out = set(self.by_lib.get(safe(lib), ()))
        for r in self.reexports.get(lib, ()):
            out |= self.closure(r, seen)
        return out

    def exports(self, lib: str, symbol: str):
        """True/False if this source can say, None if it has no data for lib."""
        key = safe(lib)
        if key not in self.by_lib:
            if self.images is not None and lib not in self.images:
                return False  # library absent entirely
            return None
        return symbol in self.closure(lib)


def load_reexports(pattern: str) -> dict:
    """reexport edges recorded by the evidence collectors."""
    out = {}
    for f in sorted(SDK.glob(pattern)):
        try:
            doc = json.loads(f.read_text())
        except Exception:
            continue
        for lib, rec in doc.items():
            if isinstance(rec, dict) and rec.get("reexports"):
                out.setdefault(lib, [])
                for r in rec["reexports"]:
                    if r not in out[lib]:
                        out[lib].append(r)
    return out


def find_universe(prefix: str, label_out: list) -> Universe:
    dirs = sorted(EXPORTS.glob(prefix + "*"))
    if not dirs:
        return Universe(EXPORTS / (prefix + "none"), prefix)
    d = dirs[-1]
    label_out.append(d.name)
    reex = load_reexports(d.name.replace("device_", "device_").replace("sdk_", "") + "*libraries.json")
    if not reex:
        reex = load_reexports("*libraries.json")
    return Universe(d, d.name, reex)


def classify_symbol(sym: str):
    if sym.startswith(CLASS_PREFIX):
        return "objc_class", sym[len(CLASS_PREFIX):]
    if sym.startswith(METACLASS_PREFIX):
        return "objc_metaclass", sym[len(METACLASS_PREFIX):]
    if sym.startswith(IVAR_PREFIX):
        return "objc_ivar", sym[len(IVAR_PREFIX):]
    return "symbol", sym


def main():
    labels = {"device": [], "tvossdk": [], "iossdk": []}
    device = find_universe("device_", labels["device"])
    tvossdk = find_universe("sdk_tvos", labels["tvossdk"])
    iossdk = find_universe("sdk_ios", labels["iossdk"])
    bundle = Universe(EXPORTS / "bundle", "bundle")

    # bundled frameworks are addressed by their LC_ID_DYLIB install name
    bundle_index = {}
    idx = EXPORTS / "bundle" / "_index.json"
    if idx.is_file():
        for rel_path, rec in json.loads(idx.read_text()).items():
            if rec.get("install_name"):
                bundle_index[rec["install_name"]] = rel_path.replace("/", "__")

    # selectors Roblox references, per binary, to attach to missing classes
    selrefs_by_binary = {}
    for f in sorted(IMPORTS.glob("*.json")):
        if f.name == "summary.json":
            continue
        d = json.loads(f.read_text())
        selrefs_by_binary[d["binary"]] = set(d.get("selrefs", []))
    all_selrefs = set().union(*selrefs_by_binary.values()) if selrefs_by_binary else set()

    findings = []
    missing_frameworks = defaultdict(list)   # lib -> [binaries]
    missing_symbols = defaultdict(lambda: defaultdict(list))  # lib -> sym -> [binaries]
    weak_missing = defaultdict(lambda: defaultdict(list))
    unverified = defaultdict(lambda: defaultdict(list))
    private_on_tvos = defaultdict(set)
    not_in_ios_sdk = defaultdict(set)

    for f in sorted(IMPORTS.glob("*.json")):
        if f.name == "summary.json":
            continue
        d = json.loads(f.read_text())
        binary = d["binary"]
        for lib, entries in d["imports"].items():
            is_system = lib.startswith("/")
            is_bundled = lib.startswith("@rpath/") or lib.startswith("@loader_path/") \
                or lib.startswith("@executable_path/")
            for e in entries:
                sym, weak = e["name"], e["weak"]
                if is_bundled:
                    key = bundle_index.get(lib)
                    present = key in bundle.by_lib if key else False
                    ok = bool(key) and sym in bundle.by_lib.get(key, set())
                    if not ok:
                        bucket = weak_missing if weak else missing_symbols
                        bucket[lib][sym].append(binary)
                    continue
                if not is_system:
                    # <ordinal N>: flat-namespace or self reference; cannot be
                    # attributed to a library, so it is recorded as unverified
                    unverified[lib][sym].append(binary)
                    continue

                lib_on_device = device.has_library(lib)
                if lib_on_device is None:
                    unverified[lib][sym].append(binary)
                    continue
                if lib_on_device is False:
                    (weak_missing if weak else missing_symbols)[lib][sym].append(binary)
                    missing_frameworks[lib].append(binary)
                    continue

                dev = device.exports(lib, sym)
                if dev is None:
                    unverified[lib][sym].append(binary)
                    continue
                if dev is False:
                    (weak_missing if weak else missing_symbols)[lib][sym].append(binary)
                    continue
                # resolves on device: is it public on tvOS?
                pub = tvossdk.exports(lib, sym)
                if pub is False:
                    private_on_tvos[lib].add(sym)

    # confirm each missing item exists on iOS, to rule out a parsing error
    for lib, syms in missing_symbols.items():
        for sym in syms:
            if iossdk.present():
                v = iossdk.exports(lib, sym)
                if v is False:
                    not_in_ios_sdk[lib].add(sym)

    # group missing ObjC classes with the selectors Roblox references
    missing_classes = {}
    for lib, syms in missing_symbols.items():
        for sym, binaries in syms.items():
            kind, name = classify_symbol(sym)
            if kind in ("objc_class", "objc_metaclass"):
                rec = missing_classes.setdefault(name, {
                    "library": lib, "imported_by": [], "metaclass": False,
                    "class_symbol": False})
                rec["imported_by"] = sorted(set(rec["imported_by"]) | set(binaries))
                if kind == "objc_metaclass":
                    rec["metaclass"] = True
                else:
                    rec["class_symbol"] = True

    # Attach referenced selectors. Selector references are not attributed to a
    # class in the binary, so this is the set Roblox uses anywhere, narrowed by
    # the binaries that import the class. Phase 3 records it as such.
    for name, rec in missing_classes.items():
        sels = set()
        for b in rec["imported_by"]:
            sels |= selrefs_by_binary.get(b, set())
        rec["candidate_selectors_in_importing_binaries"] = len(sels)

    out = {
        "evidence_sources": {
            "device": labels["device"][0] if labels["device"] else None,
            "tvos_sdk": labels["tvossdk"][0] if labels["tvossdk"] else None,
            "ios_sdk": labels["iossdk"][0] if labels["iossdk"] else None,
            "device_present": device.present(),
            "tvos_sdk_present": tvossdk.present(),
            "ios_sdk_present": iossdk.present(),
        },
        "missing_frameworks": {k: sorted(set(v)) for k, v in sorted(missing_frameworks.items())},
        "missing_symbols": {
            lib: {s: sorted(set(b)) for s, b in sorted(syms.items())}
            for lib, syms in sorted(missing_symbols.items())
        },
        "missing_classes": dict(sorted(missing_classes.items())),
        "weak_missing": {
            lib: {s: sorted(set(b)) for s, b in sorted(syms.items())}
            for lib, syms in sorted(weak_missing.items())
        },
        "unverified": {
            lib: {s: sorted(set(b)) for s, b in sorted(syms.items())}
            for lib, syms in sorted(unverified.items())
        },
        "private_on_tvos": {k: sorted(v) for k, v in sorted(private_on_tvos.items())},
        "missing_but_absent_from_ios_sdk_too": {k: sorted(v) for k, v in sorted(not_in_ios_sdk.items())},
        "totals": {},
    }
    out["totals"] = {
        "missing_frameworks": len(out["missing_frameworks"]),
        "missing_symbols": sum(len(v) for v in out["missing_symbols"].values()),
        "missing_classes": len(out["missing_classes"]),
        "weak_missing": sum(len(v) for v in out["weak_missing"].values()),
        "unverified": sum(len(v) for v in out["unverified"].values()),
        "private_on_tvos": sum(len(v) for v in out["private_on_tvos"].values()),
        "selectors_referenced_total": len(all_selrefs),
    }
    (EVIDENCE / "missing.json").write_text(json.dumps(out, indent=1))

    print("evidence sources:", json.dumps(out["evidence_sources"], indent=1))
    print("totals:", json.dumps(out["totals"], indent=1))
    if not device.present():
        print("\n*** device evidence missing: nothing can be called missing yet. ***")
        print("    Run the device-evidence workflow and fetch the outputs branch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
