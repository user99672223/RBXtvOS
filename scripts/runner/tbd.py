"""Minimal .tbd (text-based dylib stub) reader for TBD v1-v5.

Runs on the macOS Actions runner against Apple SDKs. Only derived text leaves the
runner (see CLAUDE.md "Hard data rules"): symbol names, install names, re-export
lists. No SDK bytes are ever written to the repo.

TBD v5 is JSON. TBD v1-v4 are YAML documents. PyYAML is used when available; a
small fallback parser handles the subset of YAML that Apple's v4 stubs actually
use, so the runner does not hard-depend on pip.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

try:  # pragma: no cover - availability depends on runner image
    import yaml  # type: ignore

    HAVE_YAML = True
except Exception:  # pragma: no cover
    HAVE_YAML = False


# --------------------------------------------------------------------------- v5

def _v5(doc: dict) -> dict:
    """TBD v5 (JSON): {"main_library": {...}, "libraries": [...]}"""
    out = {"install_names": [], "reexports": [], "symbols": set(),
           "objc_classes": set(), "objc_ivars": set(), "targets": set()}
    libs = []
    if "main_library" in doc:
        libs.append(doc["main_library"])
    libs.extend(doc.get("libraries", []) or [])
    for lib in libs:
        for n in lib.get("install_names", []) or []:
            if n.get("name"):
                out["install_names"].append(n["name"])
        for t in lib.get("target_info", []) or []:
            tgt = t.get("target")
            if tgt:
                out["targets"].add(tgt)
        for r in lib.get("reexported_libraries", []) or []:
            out["reexports"].extend(r.get("names", []) or [])
        for group in ("exported_symbols", "reexported_symbols"):
            for entry in lib.get(group, []) or []:
                syms = entry.get("text", {}) or {}
                data = entry.get("data", {}) or {}
                for blob in (syms, data):
                    for g in blob.get("global", []) or []:
                        out["symbols"].add(g)
                    for w in blob.get("weak", []) or []:
                        out["symbols"].add(w)
                    for t in blob.get("thread_local", []) or []:
                        out["symbols"].add(t)
                    for c in blob.get("objc_class", []) or []:
                        out["objc_classes"].add(c)
                    for c in blob.get("objc_eh_type", []) or []:
                        out["objc_classes"].add(c)
                    for iv in blob.get("objc_ivar", []) or []:
                        out["objc_ivars"].add(iv)
    return out


# ------------------------------------------------------------------------- v1-v4

def _collect_v4_exports(node, out):
    """Walk an exports/reexports list of dicts holding symbols/objc-classes."""
    for entry in node or []:
        if not isinstance(entry, dict):
            continue
        for key in ("symbols", "weak-symbols", "weak-def-symbols",
                    "thread-local-symbols"):
            for s in entry.get(key, []) or []:
                out["symbols"].add(s)
        for key in ("objc-classes", "objc-eh-types"):
            for c in entry.get(key, []) or []:
                out["objc_classes"].add(c)
        for iv in entry.get("objc-ivars", []) or []:
            out["objc_ivars"].add(iv)
        for r in entry.get("re-exports", []) or []:
            out["reexports"].append(r)


def _v4(doc: dict) -> dict:
    out = {"install_names": [], "reexports": [], "symbols": set(),
           "objc_classes": set(), "objc_ivars": set(), "targets": set()}
    if doc.get("install-name"):
        out["install_names"].append(doc["install-name"])
    for t in doc.get("targets", []) or []:
        out["targets"].add(t)
    for a in doc.get("archs", []) or []:
        out["targets"].add(a)
    _collect_v4_exports(doc.get("exports"), out)
    _collect_v4_exports(doc.get("reexports"), out)
    # v1/v2/v3 nest everything under top-level "exports" too; also handle
    # "allowable-clients"/"reexported-libraries" spellings.
    for r in doc.get("reexported-libraries", []) or []:
        if isinstance(r, dict):
            out["reexports"].extend(r.get("libraries", []) or [])
    for lib in doc.get("libraries", []) or []:
        if isinstance(lib, dict):
            sub = _v4(lib)
            for k in ("symbols", "objc_classes", "objc_ivars", "targets"):
                out[k] |= sub[k]
            out["install_names"].extend(sub["install_names"])
            out["reexports"].extend(sub["reexports"])
    return out


_KEY = re.compile(r"^(\s*)([A-Za-z0-9_-]+):\s*(.*)$")
_LIST_INLINE = re.compile(r"^\[(.*)\]$")


def _fallback_yaml(text: str) -> dict:
    """Parse the subset of YAML Apple's v4 .tbd files use.

    Handles: `key: value`, `key: [a, b]`, block lists of scalars, and lists of
    mappings introduced by `- key: value`. Good enough to recover install-name,
    targets/archs, exports/reexports symbol and objc-class lists, which is all
    this project needs. Returns the merged union across documents.
    """
    merged = {"install-name": None, "targets": [], "archs": [],
              "exports": [], "reexports": [], "reexported-libraries": []}
    # Union all symbol-ish lists regardless of nesting depth: for availability
    # questions we only care whether a name is exported at all for arm64.
    syms, objc, ivars, reex = set(), set(), set(), []
    cur_key = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#") or line.startswith("---") or line.startswith("..."):
            continue
        stripped = line.strip()
        # `- key: value` starts a new mapping in a list
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        m = _KEY.match("  " + stripped if not stripped.startswith(" ") else stripped)
        if m:
            key, val = m.group(2), m.group(3).strip()
            cur_key = key
            if key == "install-name" and val:
                merged["install-name"] = merged["install-name"] or val.strip("'\"")
            inline = _LIST_INLINE.match(val)
            items = []
            if inline:
                items = [x.strip().strip("'\"") for x in inline.group(1).split(",") if x.strip()]
            elif val and val not in ("|", ">"):
                items = [val.strip("'\"")]
            _absorb(key, items, syms, objc, ivars, reex, merged)
            continue
        # continuation of a block list: `  - name`
        if stripped.startswith("-"):
            item = stripped.lstrip("-").strip().strip("'\"")
            if item and cur_key:
                _absorb(cur_key, [item], syms, objc, ivars, reex, merged)
    return {"__fallback__": True, "install-name": merged["install-name"],
            "targets": merged["targets"], "archs": merged["archs"],
            "symbols": syms, "objc_classes": objc, "objc_ivars": ivars,
            "reexports": reex}


def _absorb(key, items, syms, objc, ivars, reex, merged):
    if key in ("symbols", "weak-symbols", "weak-def-symbols", "thread-local-symbols"):
        syms.update(items)
    elif key in ("objc-classes", "objc-eh-types"):
        objc.update(items)
    elif key == "objc-ivars":
        ivars.update(items)
    elif key in ("re-exports", "libraries"):
        reex.extend(items)
    elif key == "targets":
        merged["targets"].extend(items)
    elif key == "archs":
        merged["archs"].extend(items)


# ------------------------------------------------------------------------ public

def read(path: Path) -> dict:
    """Return {install_names, reexports, symbols, objc_classes, objc_ivars,
    targets, tbd_version, parser}. Symbol sets are plain names as they appear in
    the stub: `objc_classes` hold bare class names (no `_OBJC_CLASS_$_` prefix),
    `symbols` hold linker names (usually with a leading underscore)."""
    text = Path(path).read_text(errors="replace")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        doc = json.loads(text)
        out = _v5(doc)
        out["tbd_version"] = doc.get("tapi_tbd_version", 5)
        out["parser"] = "json-v5"
        return _finish(out)
    if HAVE_YAML:
        docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
        if docs:
            out = None
            for d in docs:
                sub = _v4(d)
                if out is None:
                    out = sub
                else:
                    for k in ("symbols", "objc_classes", "objc_ivars", "targets"):
                        out[k] |= sub[k]
                    out["install_names"].extend(sub["install_names"])
                    out["reexports"].extend(sub["reexports"])
            out["tbd_version"] = docs[0].get("tbd-version", 1)
            out["parser"] = "yaml"
            return _finish(out)
    fb = _fallback_yaml(text)
    out = {"install_names": [fb["install-name"]] if fb["install-name"] else [],
           "reexports": fb["reexports"], "symbols": fb["symbols"],
           "objc_classes": fb["objc_classes"], "objc_ivars": fb["objc_ivars"],
           "targets": set(fb["targets"]) | set(fb["archs"]),
           "tbd_version": "unknown", "parser": "fallback"}
    return _finish(out)


def _finish(out: dict) -> dict:
    out["symbols"] = sorted(out["symbols"])
    out["objc_classes"] = sorted(out["objc_classes"])
    out["objc_ivars"] = sorted(out["objc_ivars"])
    out["targets"] = sorted(out["targets"])
    out["install_names"] = sorted(set(n for n in out["install_names"] if n))
    out["reexports"] = sorted(set(out["reexports"]))
    return out


def linker_names(info: dict) -> set:
    """All names a linker could resolve from this stub, in Mach-O import spelling."""
    names = set(info["symbols"])
    for c in info["objc_classes"]:
        names.add(f"_OBJC_CLASS_$_{c}")
        names.add(f"_OBJC_METACLASS_$_{c}")
    for iv in info["objc_ivars"]:
        names.add(f"_OBJC_IVAR_$_{iv}")
    return names
