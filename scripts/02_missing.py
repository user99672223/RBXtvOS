#!/usr/bin/env python3
"""Phase 2.3: compute evidence/missing.json, the shim list.

Resolution model
----------------
Roblox's Mach-Os are two-level-namespace bound: every import names both a
symbol and the library it must come from, so an import resolves if and only if
that library exports the symbol, or re-exports another library that does. This
mirrors what dyld does at load time, which is what the Phase 5 pass condition
("zero unresolved non-weak imports") actually tests.

Getting the *library* right matters as much as the symbol:

  /System/...        a system library: answered by the device cache.
  @rpath/X           resolved by expanding each LC_RPATH of the importing
                     binary and of the main executable, in order. Most of
                     Roblox's binaries list `/usr/lib/swift` first, so
                     `@rpath/libswiftCore.dylib` is the OS Swift runtime, not
                     something in the bundle. Treating @rpath as bundle-only
                     made 423 OS Swift symbols look missing.
  @executable_path/  relative to the .app.
  @loader_path/      relative to the importing binary.
  <flat-namespace>   the few flat-namespace imports dyld resolves by searching
                     every loaded image; checked against the union.
  <self>             resolved inside the importing binary.

Export universes, one per CLAUDE.md evidence source:

  device  evidence/exports/device_<build>/   source 1, the authority on what
                                             exists on the Apple TV
  tvossdk evidence/exports/sdk_tvos<ver>/    source 2, the public contract
  iossdk  evidence/exports/sdk_ios<ver>/     source 3, used only to confirm a
                                             missing item really exists on iOS,
                                             which rules out a parsing error
  bundle  evidence/exports/bundle/           Roblox's own frameworks

Outcomes per import:

  ok                  resolves on the device (or in the bundle)
  MISSING_FRAMEWORK   the library itself is absent from the device
  MISSING_SYMBOL      the library is present but does not export the symbol
  weak-missing        missing but weakly imported, so dyld binds it to NULL and
                      the app still loads; recorded, never shimmed
  lazy-missing        missing but LAZILY bound: dyld leaves it unbound until
                      the first call, so it cannot stop the app launching. It
                      aborts only if that code path runs. Recorded separately
                      so the pass condition counts launch blockers and not
                      these.
  UNVERIFIED          no evidence source can answer for that library

The Phase 5 pass condition is therefore "zero unresolved non-weak EAGER
imports". Counting lazy ones would fail a build that launches perfectly; not
counting them at all would hide a real call-time abort.

Separately flagged: symbols the device exports that the tvOS SDK does not.
Those link and run but have no public contract.
"""
from __future__ import annotations

import json
import posixpath
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_macho import EVIDENCE  # noqa: E402

IMPORTS = EVIDENCE / "imports"
EXPORTS = EVIDENCE / "exports"
SDK = EVIDENCE / "sdk"
SOURCES_CONFIG = EVIDENCE / "queries" / "evidence_sources.json"

CLASS_PREFIX = "_OBJC_CLASS_$_"
METACLASS_PREFIX = "_OBJC_METACLASS_$_"
IVAR_PREFIX = "_OBJC_IVAR_$_"

FLAT_BUCKETS = ("<flat-namespace>", "<executable>", "<ordinal -3>", "<ordinal ?>")


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
        self._union: set | None = None
        if not directory.is_dir():
            return
        imgs = directory / "_images.txt"
        if imgs.is_file():
            self.images = {l.strip() for l in imgs.read_text().splitlines() if l.strip()}
        for f in sorted(directory.glob("*.txt")):
            if f.name.startswith("_"):
                continue
            self.by_lib[f.stem] = {l.strip() for l in f.read_text().splitlines() if l.strip()}

    def present(self) -> bool:
        return bool(self.by_lib) or bool(self.images)

    def has_library(self, lib: str):
        """True/False if this source can say, None if it cannot."""
        if safe(lib) in self.by_lib:
            return True
        if self.images is not None:
            return lib in self.images
        return None

    def closure(self, lib: str, _seen=None) -> set:
        seen = _seen if _seen is not None else set()
        if lib in seen:
            return set()
        seen.add(lib)
        out = set(self.by_lib.get(safe(lib), ()))
        for r in self.reexports.get(lib, ()):
            out |= self.closure(r, seen)
        return out

    def exports(self, lib: str, symbol: str):
        """True/False if this source can answer for lib, else None."""
        if safe(lib) not in self.by_lib:
            if self.images is not None and lib not in self.images:
                return False
            return None
        return symbol in self.closure(lib)

    def union(self) -> set:
        if self._union is None:
            self._union = set()
            for s in self.by_lib.values():
                self._union |= s
        return self._union


def load_reexports() -> dict:
    out = {}
    for f in sorted(SDK.glob("*libraries.json")):
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


def configured_source(key: str):
    if SOURCES_CONFIG.is_file():
        return json.loads(SOURCES_CONFIG.read_text()).get(key)
    return None


def find_universe(prefix: str, key: str, label_out: list, reex: dict) -> Universe:
    name = configured_source(key)
    if name:
        d = EXPORTS / name
        if not d.is_dir():
            raise SystemExit(f"evidence_sources.json pins {key}={name!r} but {d} is missing. "
                             f"Fetch the outputs branch, or correct the pin.")
    else:
        dirs = sorted(EXPORTS.glob(prefix + "*"))
        if not dirs:
            return Universe(EXPORTS / (prefix + "none"), prefix, reex)
        if len(dirs) > 1:
            raise SystemExit(f"several candidates for {key}: {[x.name for x in dirs]}. "
                             f"Pin one in {SOURCES_CONFIG}.")
        d = dirs[0]
    label_out.append(d.name)
    return Universe(d, d.name, reex)


def classify_symbol(sym: str):
    if sym.startswith(CLASS_PREFIX):
        return "objc_class", sym[len(CLASS_PREFIX):]
    if sym.startswith(METACLASS_PREFIX):
        return "objc_metaclass", sym[len(METACLASS_PREFIX):]
    if sym.startswith(IVAR_PREFIX):
        return "objc_ivar", sym[len(IVAR_PREFIX):]
    return "symbol", sym


class Resolver:
    """Turns (importing binary, library name) into candidate export sets."""

    def __init__(self, device: Universe, bundle: Universe, records: dict, main_binary: str):
        self.device = device
        self.bundle = bundle
        self.records = records          # binary rel path -> imports json
        self.main = main_binary
        # bundle lookup by install name and by bundle-relative path
        self.by_install, self.by_relpath = {}, {}
        idx = EXPORTS / "bundle" / "_index.json"
        if idx.is_file():
            for rel_path, rec in json.loads(idx.read_text()).items():
                key = rel_path.replace("/", "__")
                self.by_relpath[rel_path] = key
                if rec.get("install_name"):
                    self.by_install[rec["install_name"]] = key

    def rpaths_for(self, binary: str):
        """LC_RPATHs dyld would search: the importing image's, then the
        executable's. dyld searches the rpaths of every image in the load
        chain, and the main executable is always in it."""
        out = []
        for b in (binary, self.main):
            for rp in (self.records.get(b, {}).get("rpaths") or []):
                if rp not in out:
                    out.append(rp)
        return out

    def candidates(self, binary: str, lib: str):
        """Yield ("system", path) or ("bundle", key) candidates, in dyld order."""
        if lib in FLAT_BUCKETS:
            yield ("flat", None)
            return
        if lib.startswith("/"):
            yield ("system", lib)
            return
        inst = self.records.get(binary, {}).get("install_name")
        if inst and lib == inst:
            key = self.by_relpath.get(binary)
            if key:
                yield ("bundle", key)
            return
        # already a bundled framework we know by install name
        if lib in self.by_install:
            yield ("bundle", self.by_install[lib])
            return

        bin_dir = posixpath.dirname(binary)
        if lib.startswith("@rpath/"):
            tail = lib[len("@rpath/"):]
            for rp in self.rpaths_for(binary):
                if rp.startswith("/"):
                    yield ("system", posixpath.normpath(f"{rp}/{tail}"))
                elif rp.startswith("@executable_path/"):
                    sub = rp[len("@executable_path/"):]
                    yield ("bundle", self.by_relpath.get(
                        posixpath.normpath(f"{sub}/{tail}"), None))
                elif rp.startswith("@loader_path/"):
                    sub = rp[len("@loader_path/"):]
                    yield ("bundle", self.by_relpath.get(
                        posixpath.normpath(f"{bin_dir}/{sub}/{tail}"), None))
            # dyld also finds a bundled framework under the app's Frameworks
            # even when the rpath list is empty (the loading chain supplies it)
            yield ("bundle", self.by_relpath.get(f"Frameworks/{tail}", None))
            yield ("system", f"/usr/lib/swift/{tail}")
            return
        if lib.startswith("@executable_path/"):
            sub = lib[len("@executable_path/"):]
            yield ("bundle", self.by_relpath.get(posixpath.normpath(sub), None))
            return
        if lib.startswith("@loader_path/"):
            sub = lib[len("@loader_path/"):]
            yield ("bundle", self.by_relpath.get(posixpath.normpath(f"{bin_dir}/{sub}"), None))
            return
        yield ("unknown", lib)

    def resolve(self, binary: str, lib: str, sym: str):
        """Return (status, detail) with status in ok / MISSING_SYMBOL /
        MISSING_FRAMEWORK / UNVERIFIED."""
        saw_library = False
        tried = []
        for kind, target in self.candidates(binary, lib):
            if kind == "flat":
                if sym in self.device.union() or sym in self.bundle.union():
                    return "ok", "flat-namespace union"
                return "MISSING_SYMBOL", "not found in any loaded image"
            if target is None:
                continue
            tried.append(f"{kind}:{target}")
            if kind == "system":
                has = self.device.has_library(target)
                if has is None:
                    continue
                if not has:
                    continue
                saw_library = True
                if sym in self.device.closure(target):
                    return "ok", f"device:{target}"
            elif kind == "bundle":
                if target not in self.bundle.by_lib:
                    continue
                saw_library = True
                if sym in self.bundle.by_lib[target]:
                    return "ok", f"bundle:{target}"
            else:
                continue
        if not tried:
            return "UNVERIFIED", f"no candidate path for {lib}"
        if saw_library:
            return "MISSING_SYMBOL", "; ".join(tried)
        return "MISSING_FRAMEWORK", "; ".join(tried)


def main():
    labels = {"device": [], "tvossdk": [], "iossdk": []}
    reex = load_reexports()
    device = find_universe("device_", "device", labels["device"], reex)
    tvossdk = find_universe("sdk_tvos", "tvos_sdk", labels["tvossdk"], reex)
    iossdk = find_universe("sdk_ios", "ios_sdk", labels["iossdk"], reex)
    bundle = Universe(EXPORTS / "bundle", "bundle")

    records, selrefs_by_binary = {}, {}
    for f in sorted(IMPORTS.glob("*.json")):
        if f.name == "summary.json":
            continue
        d = json.loads(f.read_text())
        records[d["binary"]] = d
        selrefs_by_binary[d["binary"]] = set(d.get("selrefs", []))

    main_binary = "Roblox" if "Roblox" in records else next(iter(records), "")
    resolver = Resolver(device, bundle, records, main_binary)

    missing_frameworks = defaultdict(set)
    missing_symbols = defaultdict(lambda: defaultdict(set))
    weak_missing = defaultdict(lambda: defaultdict(set))
    lazy_missing = defaultdict(lambda: defaultdict(set))
    unverified = defaultdict(lambda: defaultdict(set))
    private_on_tvos = defaultdict(set)
    not_in_ios_sdk = defaultdict(set)
    details = {}
    ok_count = 0

    for binary, d in records.items():
        for lib, entries in d["imports"].items():
            for e in entries:
                sym, weak = e["name"], e["weak"]
                lazy = bool(e.get("lazy", False))
                status, detail = resolver.resolve(binary, lib, sym)
                if status == "ok":
                    ok_count += 1
                    if lib.startswith("/"):
                        pub = tvossdk.exports(lib, sym)
                        if pub is False:
                            private_on_tvos[lib].add(sym)
                    continue
                details.setdefault(f"{lib}|{sym}", detail)
                if weak:
                    weak_missing[lib][sym].add(binary)
                    continue
                if lazy:
                    # not a launch blocker: dyld binds it on first call
                    lazy_missing[lib][sym].add(binary)
                    continue
                if status == "UNVERIFIED":
                    unverified[lib][sym].add(binary)
                elif status == "MISSING_FRAMEWORK":
                    missing_frameworks[lib].add(binary)
                    missing_symbols[lib][sym].add(binary)
                else:
                    missing_symbols[lib][sym].add(binary)

    # confirm each missing item exists on iOS, to rule out a parsing error
    for lib, syms in missing_symbols.items():
        if not iossdk.present() or not lib.startswith("/"):
            continue
        for sym in syms:
            if iossdk.exports(lib, sym) is False:
                not_in_ios_sdk[lib].add(sym)

    missing_classes = {}
    for lib, syms in missing_symbols.items():
        for sym, binaries in syms.items():
            kind, name = classify_symbol(sym)
            if kind in ("objc_class", "objc_metaclass"):
                rec = missing_classes.setdefault(name, {
                    "library": lib, "imported_by": set(), "metaclass": False,
                    "class_symbol": False})
                rec["imported_by"] |= binaries
                if kind == "objc_metaclass":
                    rec["metaclass"] = True
                else:
                    rec["class_symbol"] = True
    for name, rec in missing_classes.items():
        rec["imported_by"] = sorted(rec["imported_by"])
        sels = set()
        for b in rec["imported_by"]:
            sels |= selrefs_by_binary.get(b, set())
        rec["selectors_referenced_by_importing_binaries"] = len(sels)
        rec["in_ios_sdk"] = bool(
            iossdk.exports(rec["library"], CLASS_PREFIX + name))

    out = {
        "evidence_sources": {
            "device": labels["device"][0] if labels["device"] else None,
            "tvos_sdk": labels["tvossdk"][0] if labels["tvossdk"] else None,
            "ios_sdk": labels["iossdk"][0] if labels["iossdk"] else None,
            "device_present": device.present(),
            "tvos_sdk_present": tvossdk.present(),
            "ios_sdk_present": iossdk.present(),
        },
        "resolved_ok": ok_count,
        "missing_frameworks": {k: sorted(v) for k, v in sorted(missing_frameworks.items())},
        "missing_symbols": {lib: {s: sorted(b) for s, b in sorted(syms.items())}
                            for lib, syms in sorted(missing_symbols.items())},
        "missing_classes": dict(sorted(missing_classes.items())),
        "weak_missing": {lib: {s: sorted(b) for s, b in sorted(syms.items())}
                         for lib, syms in sorted(weak_missing.items())},
        "lazy_missing": {lib: {s: sorted(b) for s, b in sorted(syms.items())}
                         for lib, syms in sorted(lazy_missing.items())},
        "unverified": {lib: {s: sorted(b) for s, b in sorted(syms.items())}
                       for lib, syms in sorted(unverified.items())},
        "private_on_tvos": {k: sorted(v) for k, v in sorted(private_on_tvos.items())},
        "missing_but_absent_from_ios_sdk_too": {k: sorted(v) for k, v in sorted(not_in_ios_sdk.items())},
        "resolution_detail": details,
    }
    out["totals"] = {
        "resolved_ok": ok_count,
        "missing_frameworks": len(out["missing_frameworks"]),
        "missing_symbols": sum(len(v) for v in out["missing_symbols"].values()),
        "missing_classes": len(out["missing_classes"]),
        "weak_missing": sum(len(v) for v in out["weak_missing"].values()),
        "lazy_missing": sum(len(v) for v in out["lazy_missing"].values()),
        "unverified": sum(len(v) for v in out["unverified"].values()),
        "private_on_tvos": sum(len(v) for v in out["private_on_tvos"].values()),
    }
    (EVIDENCE / "missing.json").write_text(json.dumps(out, indent=1))
    print("evidence sources:", json.dumps(out["evidence_sources"], indent=1))
    print("totals:", json.dumps(out["totals"], indent=1))
    if not device.present():
        print("\n*** device evidence missing: nothing can be called missing yet ***")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
