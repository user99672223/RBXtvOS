#!/usr/bin/env python3
"""Phase 2 evidence from Apple SDKs, run on the macOS Actions runner.

Two subcommands, matching CLAUDE.md evidence sources 2 and 3:

  tvos  <sdk_path>   source 2: the tvOS SDK. Export lists from every .tbd the
                     queried Roblox libraries map to, plus header availability
                     annotations for the queried ObjC classes and symbols.
  ios   <sdk_path>   source 3: the iOS SDK Roblox was built against. Export
                     lists (to rule out parse errors) and the *contract* of each
                     queried class: header path, superclass, protocols, and the
                     declarations of the selectors Roblox references.

Input is evidence/queries/sdk_query.json (derived Roblox metadata, committed).
Output is derived text only, under evidence/. No SDK bytes are copied.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tbd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "evidence"
QUERY = EVIDENCE / "queries" / "sdk_query.json"


# --------------------------------------------------------------------------- util

def sdk_rel_candidates(lib_path: str):
    """Map a Mach-O install name to candidate .tbd paths inside an SDK.

    A framework binary has no extension, so the stub just gains one:
      /System/Library/Frameworks/UIKit.framework/UIKit
        -> System/Library/Frameworks/UIKit.framework/UIKit.tbd

    A dylib's stub *replaces* the extension rather than appending to it:
      /usr/lib/libSystem.B.dylib        -> usr/lib/libSystem.B.tbd
      /usr/lib/swift/libswiftCore.dylib -> usr/lib/swift/libswiftCore.tbd

    Getting that wrong made every /usr/lib and /usr/lib/swift entry look
    absent from both SDKs, which the iOS cross-check (CLAUDE.md Phase 2.3,
    "rule out parsing errors") is there to catch.
    """
    p = lib_path.lstrip("/")
    if p.endswith(".dylib"):
        yield p[: -len(".dylib")] + ".tbd"
    yield p + ".tbd"
    if "/Frameworks/" in p:
        base = p.replace("/Frameworks/", "/PrivateFrameworks/")
        if base.endswith(".dylib"):
            yield base[: -len(".dylib")] + ".tbd"
        yield base + ".tbd"
    # a few libraries ship as a real file in the SDK rather than a stub
    yield p


def find_tbd(sdk: Path, lib_path: str):
    for rel in sdk_rel_candidates(lib_path):
        cand = sdk / rel
        if cand.is_file():
            return cand
    return None


def header_dirs(sdk: Path):
    for d in ("System/Library/Frameworks", "System/Library/PrivateFrameworks",
              "usr/include"):
        q = sdk / d
        if q.is_dir():
            yield q


AVAIL_TOKENS = (
    "API_UNAVAILABLE", "API_AVAILABLE", "API_DEPRECATED",
    "__TVOS_PROHIBITED", "__TVOS_AVAILABLE", "TVOS_UNAVAILABLE",
    "NS_CLASS_AVAILABLE_IOS", "NS_CLASS_DEPRECATED_IOS",
    "UIKIT_AVAILABLE_TVOS_ONLY", "UIKIT_CLASS_AVAILABLE_IOS_TVOS",
    "NS_UNAVAILABLE", "NS_EXTENSION_UNAVAILABLE",
)


def grep_headers(sdk: Path, pattern: str, extra_args=()):
    """Run grep over the SDK's framework headers. Returns list of (path, line)."""
    dirs = [str(d) for d in header_dirs(sdk)]
    if not dirs:
        return []
    cmd = ["grep", "-rn", "--include=*.h", "--include=*.apinotes", *extra_args,
           "-E", pattern, *dirs]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3:
            out.append((os.path.relpath(parts[0], sdk), parts[1], parts[2].strip()))
    return out


def context_around(sdk: Path, rel_path: str, lineno: int, before=3, after=6):
    f = sdk / rel_path
    try:
        lines = f.read_text(errors="replace").splitlines()
    except OSError:
        return []
    i = max(0, lineno - 1 - before)
    j = min(len(lines), lineno - 1 + after + 1)
    return [l.rstrip() for l in lines[i:j]]


# ------------------------------------------------------------------- tvOS (src 2)

def run_tvos(sdk: Path, out_dir: Path, label: str):
    q = json.loads(QUERY.read_text())
    out_dir.mkdir(parents=True, exist_ok=True)
    exports_dir = out_dir / "exports" / f"sdk_{label}"
    exports_dir.mkdir(parents=True, exist_ok=True)

    per_lib = {}
    for lib in q["libraries"]:
        t = find_tbd(sdk, lib)
        rec = {"install_name": lib, "tbd": None, "present": False,
               "symbols": 0, "objc_classes": 0, "reexports": [], "targets": [],
               "tbd_version": None, "parser": None}
        if t is not None:
            try:
                info = tbd.read(t)
            except Exception as e:  # keep going; a single bad stub is recorded
                rec.update(present=True, tbd=str(t.relative_to(sdk)),
                           error=f"{type(e).__name__}: {e}")
                per_lib[lib] = rec
                print(f"  !! {lib}: {rec['error']}")
                continue
            rec.update(present=True, tbd=str(t.relative_to(sdk)),
                       symbols=len(info["symbols"]),
                       objc_classes=len(info["objc_classes"]),
                       reexports=info["reexports"], targets=info["targets"],
                       tbd_version=info["tbd_version"], parser=info["parser"],
                       install_names=info["install_names"])
            safe = lib.lstrip("/").replace("/", "__")
            names = sorted(tbd.linker_names(info))
            (exports_dir / f"{safe}.txt").write_text("\n".join(names) + "\n")
        per_lib[lib] = rec
    (out_dir / "sdk" / f"{label}_libraries.json").parent.mkdir(parents=True, exist_ok=True)
    (out_dir / "sdk" / f"{label}_libraries.json").write_text(json.dumps(per_lib, indent=1))

    # header availability for queried ObjC classes
    wanted = sorted({c for lst in q["classes"].values() for c in lst})
    avail = {}
    if wanted:
        # one grep for all @interface declarations, then filter
        hits = grep_headers(sdk, r"^\s*(@interface|@protocol)\s+[A-Za-z_][A-Za-z0-9_]*")
        by_class = {}
        for rel, ln, text in hits:
            m = re.match(r"^\s*@(?:interface|protocol)\s+([A-Za-z_][A-Za-z0-9_]*)", text)
            if m:
                by_class.setdefault(m.group(1), []).append((rel, int(ln), text))
        for cls in wanted:
            decls = by_class.get(cls, [])
            entries = []
            for rel, ln, text in decls[:4]:
                ctx = context_around(sdk, rel, ln)
                toks = sorted({t for t in AVAIL_TOKENS
                               if any(t in c for c in ctx)})
                entries.append({"header": rel, "line": ln, "decl": text,
                                "availability_tokens": toks, "context": ctx})
            avail[cls] = {"declared_in_headers": bool(decls), "decls": entries}
    (out_dir / "sdk" / f"{label}_class_availability.json").write_text(json.dumps(avail, indent=1))

    print(f"[{label}] libraries: {sum(1 for v in per_lib.values() if v['present'])}/{len(per_lib)} have a .tbd")
    print(f"[{label}] classes queried: {len(wanted)}, declared in headers: {sum(1 for v in avail.values() if v['declared_in_headers'])}")

    run_header_targets(sdk, out_dir, label)


def run_header_targets(sdk: Path, out_dir: Path, label: str):
    """Dump declarations this project needs the *contract* of, from this SDK.

    Driven by evidence/queries/header_targets.json. Roblox does not import the
    APIs our own cursor and logging code uses (GameController, the focus-engine
    switches, UIControl action dispatch), so they are not in sdk_query.json and
    would otherwise never be recorded. CLAUDE.md Phase 6 requires the exact
    mechanism to be verified in headers rather than assumed, which is what this
    produces.
    """
    targets_path = EVIDENCE / "queries" / "header_targets.json"
    if not targets_path.is_file():
        print(f"[{label}] no header_targets.json; skipping targeted header dump")
        return
    spec = json.loads(targets_path.read_text())
    results = {}
    for item in spec.get("targets", []):
        tid = item.get("id") or item.get("pattern", "?")
        pattern = item.get("pattern")
        if not pattern:
            continue
        before = int(item.get("before", 2))
        after = int(item.get("after", 12))
        hits = grep_headers(sdk, pattern)
        entries = []
        for rel_p, ln, text in hits[: int(item.get("max_hits", 12))]:
            entries.append({
                "header": rel_p,
                "line": int(ln),
                "match": text,
                "context": context_around(sdk, rel_p, int(ln), before, after),
            })
        results[tid] = {
            "pattern": pattern,
            "why": item.get("why", ""),
            "hit_count": len(hits),
            "hits": entries,
        }
        print(f"[{label}] header target {tid!r}: {len(hits)} hits")
    (out_dir / "sdk" / f"{label}_header_targets.json").write_text(
        json.dumps(results, indent=1))


# -------------------------------------------------------------------- iOS (src 3)

SEL_DECL = re.compile(r"^\s*[-+]\s*\(")


def method_decls_for(sdk: Path, headers: list, selectors: set):
    """Collect `- (ret)sel:...;` declarations whose selector is in `selectors`."""
    found = {}
    for rel in headers:
        f = sdk / rel
        try:
            lines = f.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            if not SEL_DECL.match(line):
                continue
            # join continuation lines until ';'
            buf, j = line.rstrip(), i
            while ";" not in buf and j + 1 < len(lines) and j - i < 6:
                j += 1
                buf += " " + lines[j].strip()
            sel = selector_of(buf)
            if sel and sel in selectors:
                found.setdefault(sel, []).append({"header": rel, "line": i + 1,
                                                  "decl": " ".join(buf.split())})
    return found


def selector_of(decl: str):
    """Extract the selector from an ObjC method declaration string."""
    s = decl.strip()
    if not s or s[0] not in "+-":
        return None
    s = s[1:].strip()
    if s.startswith("("):  # skip return type
        depth, k = 0, 0
        for k, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
        s = s[k + 1:]
    s = s.split(";")[0]
    parts = []
    # tokens like `name:(Type)arg name2:(Type)arg2`  or a bare `name`
    i = 0
    while i < len(s):
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*(:)?", s[i:])
        if not m:
            break
        name, colon = m.group(1), m.group(2)
        i += m.end()
        if colon:
            parts.append(name + ":")
            # skip the (Type)argname that follows
            m2 = re.match(r"\s*\(", s[i:])
            if m2:
                depth, k = 0, 0
                sub = s[i:]
                for k, ch in enumerate(sub):
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            break
                i += k + 1
            m3 = re.match(r"\s*[A-Za-z_][A-Za-z0-9_]*", s[i:])
            if m3:
                i += m3.end()
        else:
            parts.append(name)
            break
    return "".join(parts) if parts else None


def run_ios(sdk: Path, out_dir: Path, label: str, targets_file: Path | None):
    q = json.loads(QUERY.read_text())
    out_dir.mkdir(parents=True, exist_ok=True)
    exports_dir = out_dir / "exports" / f"sdk_{label}"
    exports_dir.mkdir(parents=True, exist_ok=True)

    per_lib = {}
    for lib in q["libraries"]:
        t = find_tbd(sdk, lib)
        rec = {"install_name": lib, "present": t is not None}
        if t is not None:
            try:
                info = tbd.read(t)
            except Exception as e:
                rec.update(tbd=str(t.relative_to(sdk)), error=f"{type(e).__name__}: {e}")
                per_lib[lib] = rec
                print(f"  !! {lib}: {rec['error']}")
                continue
            rec.update(tbd=str(t.relative_to(sdk)), symbols=len(info["symbols"]),
                       objc_classes=len(info["objc_classes"]),
                       reexports=info["reexports"], tbd_version=info["tbd_version"])
            safe = lib.lstrip("/").replace("/", "__")
            (exports_dir / f"{safe}.txt").write_text(
                "\n".join(sorted(tbd.linker_names(info))) + "\n")
        per_lib[lib] = rec
    (out_dir / "sdk").mkdir(parents=True, exist_ok=True)
    (out_dir / "sdk" / f"{label}_libraries.json").write_text(json.dumps(per_lib, indent=1))

    # Contracts: only for the classes we were asked about (targets_file), or all
    # queried classes when no narrowed list is supplied.
    if targets_file and targets_file.is_file():
        tgt = json.loads(targets_file.read_text())
        classes = tgt.get("classes", [])
        selectors = set(tgt.get("selectors", []))
        symbols = tgt.get("symbols", [])
    else:
        classes = sorted({c for lst in q["classes"].values() for c in lst})
        selectors = set(q["selectors"])
        symbols = []

    hits = grep_headers(sdk, r"^\s*@interface\s+[A-Za-z_][A-Za-z0-9_]*")
    by_class = {}
    for rel, ln, text in hits:
        m = re.match(r"^\s*@interface\s+([A-Za-z_][A-Za-z0-9_]*)", text)
        if m:
            by_class.setdefault(m.group(1), []).append((rel, int(ln), text))

    contracts = {}
    for cls in classes:
        decls = by_class.get(cls, [])
        base = [d for d in decls if ":" in d[2] or "(" not in d[2]]
        iface = None
        headers = sorted({d[0] for d in decls})
        for rel, ln, text in decls:
            m = re.match(r"^\s*@interface\s+" + re.escape(cls) +
                         r"\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*(<[^>]*>)?", text)
            if m:
                iface = {"header": rel, "line": ln, "decl": text.strip(),
                         "superclass": m.group(1),
                         "protocols": [p.strip() for p in (m.group(2) or "").strip("<>").split(",") if p.strip()]}
                break
        decl_map = method_decls_for(sdk, headers, selectors) if headers else {}
        contracts[cls] = {
            "declared_in_headers": bool(decls),
            "interface": iface,
            "headers": headers,
            "all_interface_lines": [{"header": r, "line": l, "decl": t.strip()} for r, l, t in decls[:8]],
            "referenced_selector_decls": decl_map,
        }
        _ = base
    (out_dir / "sdk" / f"{label}_class_contracts.json").write_text(json.dumps(contracts, indent=1))

    # function/data symbol contracts
    sym_out = {}
    for s in symbols:
        bare = s[1:] if s.startswith("_") else s
        hits = grep_headers(sdk, r"\b" + re.escape(bare) + r"\b", extra_args=("-m", "4"))
        sym_out[s] = [{"header": r, "line": int(l), "text": t} for r, l, t in hits[:8]]
    (out_dir / "sdk" / f"{label}_symbol_contracts.json").write_text(json.dumps(sym_out, indent=1))

    print(f"[{label}] libraries with .tbd: {sum(1 for v in per_lib.values() if v['present'])}/{len(per_lib)}")
    print(f"[{label}] class contracts: {sum(1 for v in contracts.values() if v['interface'])}/{len(contracts)} resolved an @interface")


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        print("usage: sdk_evidence.py {tvos|ios} <sdk_path> <label> [targets.json]")
        return 2
    mode, sdk, label = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
    targets = Path(sys.argv[4]) if len(sys.argv) > 4 else None
    assert sdk.is_dir(), f"no such SDK: {sdk}"
    if mode == "tvos":
        run_tvos(sdk, EVIDENCE, label)
    elif mode == "ios":
        run_ios(sdk, EVIDENCE, label, targets)
    else:
        print(f"unknown mode {mode}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
