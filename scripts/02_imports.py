#!/usr/bin/env python3
"""Phase 2.1: per-binary imports (source 4: Roblox's own binary metadata).

For every Mach-O in work/unpacked writes evidence/imports/<binary>.json with:
  libraries      linked dylibs (+ weak flag)
  imports        {library: [{"name", "weak"}]}   (two-level namespace: bound library per symbol)
  objc_classes   imported _OBJC_CLASS_$_ / _OBJC_METACLASS_$_ names, per library
  selrefs        selector names referenced through __objc_selrefs
  methtypes      strings from __objc_methtype (type encodings)
and a flat evidence/imports/<binary>.imports.tsv for grepping.
"""
import json
import struct
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lief  # noqa: E402
from lib_macho import EVIDENCE, app_dir, find_machos, parse, rel  # noqa: E402

OUT = EVIDENCE / "imports"


def safe_name(r):
    return r.replace("/", "__")


def cstrings(sec):
    if sec is None:
        return []
    data = bytes(sec.content)
    return [s.decode(errors="replace") for s in data.split(b"\0") if s]


def section(b, seg, name):
    for s in b.sections:
        if s.segment_name == seg and s.name == name:
            return s
    return None


def read_cstr_at(b, vaddr):
    off = b.virtual_address_to_offset(vaddr)
    if isinstance(off, lief.lief_errors) or off is None:
        return None
    raw = b.get_content_from_virtual_address(vaddr, 512)
    data = bytes(raw)
    return data.split(b"\0", 1)[0].decode(errors="replace")


def selrefs(b):
    sec = section(b, "__DATA", "__objc_selrefs") or section(b, "__DATA_CONST", "__objc_selrefs")
    if sec is None:
        return []
    base = b.imagebase
    methname = section(b, "__TEXT", "__objc_methname")
    lo, hi = (methname.virtual_address, methname.virtual_address + methname.size) if methname else (0, 0)
    mdata = bytes(methname.content) if methname else b""
    data = bytes(sec.content)
    out = set()
    for i in range(0, len(data), 8):
        v = struct.unpack_from("<Q", data, i)[0]
        cand = [v & ((1 << 36) - 1), (v & ((1 << 36) - 1)) + base, v]
        for t in cand:
            if lo <= t < hi:
                o = t - lo
                out.add(mdata[o:mdata.index(b"\0", o)].decode(errors="replace"))
                break
    return sorted(out)


def rpaths(b):
    return [c.path for c in b.commands
            if c.command == lief.MachO.LoadCommand.TYPE.RPATH]


def ordinal_table(b):
    """1-based library ordinal -> install name, in load-command order.

    Two-level-namespace imports name their library by ordinal, and the ordinal
    indexes the dylib load commands in the order they appear (1-based). LIEF
    resolves most of them itself but leaves some unmapped, which showed up as
    `<ordinal 48>` buckets that no evidence source could answer. Mapping them
    here is what makes the Phase 5 "zero unresolved non-weak imports" check
    mean anything.
    """
    DYLIB_CMDS = (
        lief.MachO.LoadCommand.TYPE.LOAD_DYLIB,
        lief.MachO.LoadCommand.TYPE.LOAD_WEAK_DYLIB,
        lief.MachO.LoadCommand.TYPE.REEXPORT_DYLIB,
        lief.MachO.LoadCommand.TYPE.LOAD_UPWARD_DYLIB,
        lief.MachO.LoadCommand.TYPE.LAZY_LOAD_DYLIB,
        lief.MachO.LoadCommand.TYPE.ID_DYLIB,
    )
    table, idx = {}, 0
    for c in b.commands:
        if c.command in DYLIB_CMDS:
            if c.command == lief.MachO.LoadCommand.TYPE.ID_DYLIB:
                # LC_ID_DYLIB is not part of the ordinal numbering
                continue
            idx += 1
            table[idx] = c.name
    return table


# Mach-O special library ordinals (mach-o/loader.h).
SELF_LIBRARY_ORDINAL = 0
MAX_LIBRARY_ORDINAL = 0xfd
DYNAMIC_LOOKUP_ORDINAL = 0xfe   # flat namespace: search every loaded image
EXECUTABLE_ORDINAL = 0xff


def resolve_ordinal(ordinal, table, self_name):
    """Turn a raw library ordinal into something an evidence source can answer."""
    try:
        o = int(ordinal)
    except (TypeError, ValueError):
        return "<ordinal ?>"
    # LIEF reports the special high ordinals as small negatives.
    if o in (DYNAMIC_LOOKUP_ORDINAL, -2):
        return "<flat-namespace>"
    if o in (EXECUTABLE_ORDINAL, -1):
        return "<executable>"
    if o == SELF_LIBRARY_ORDINAL:
        return self_name or "<self>"
    if 1 <= o <= MAX_LIBRARY_ORDINAL and o in table:
        return table[o]
    return f"<ordinal {o}>"


def main():
    app = app_dir()
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {}
    for p in find_machos(app):
        r = rel(p, app)
        b = parse(p)[0]
        libs = [{"name": l.name, "weak": "WEAK" in str(l.command)} for l in b.libraries
                if "ID_DYLIB" not in str(l.command)]
        self_name = None
        for l in b.libraries:
            if "ID_DYLIB" in str(l.command):
                self_name = l.name
                break
        table = ordinal_table(b)
        rp = rpaths(b)
        # Record the binding class per import, not just the weak flag.
        # A non-weak import that is LAZY is not a launch blocker: dyld leaves
        # it unbound until the first call, so a missing symbol only aborts if
        # the code path actually runs. Four of RobloxLib's flat-namespace MIG
        # symbols are exactly this, and treating them as launch blockers would
        # make the Phase 5 pass condition wrong in both directions.
        imports = defaultdict(dict)

        def record(lib, name, weak, lazy):
            prev = imports[lib].get(name)
            if prev is None:
                imports[lib][name] = {"weak": weak, "lazy": lazy}
            else:
                # the strictest observation wins: if any binding of this
                # symbol is non-weak and eager, that is what dyld must satisfy
                prev["weak"] = prev["weak"] and weak
                prev["lazy"] = prev["lazy"] and lazy

        for bi in b.bindings:
            if not bi.has_symbol:
                continue
            name = bi.symbol.name
            if bi.has_library:
                lib = bi.library.name
            else:
                lib = resolve_ordinal(getattr(bi, "library_ordinal", None), table, self_name)
            weak = bool(getattr(bi, "weak_import", False))
            cls = str(getattr(bi, "binding_class", ""))
            lazy = cls.endswith("LAZY")
            if cls.endswith("WEAK"):
                weak = True
            record(lib, name, weak, lazy)
        # symbols imported without a binding still matter for resolution
        for s in b.imported_symbols:
            if s.has_binding_info:
                continue
            if getattr(s, "library", None):
                lib = s.library.name
            else:
                lib = resolve_ordinal(getattr(s, "library_ordinal", None), table, self_name)
            if s.name not in imports[lib]:
                imports[lib][s.name] = {"weak": False, "lazy": False}
        imp_out = {lib: [{"name": n, "weak": v["weak"], "lazy": v["lazy"]}
                         for n, v in sorted(d.items())]
                   for lib, d in sorted(imports.items())}
        objc = {lib: sorted({n.split("$_", 1)[1] for n in d if n.startswith(("_OBJC_CLASS_$_", "_OBJC_METACLASS_$_"))})
                for lib, d in imports.items()}
        objc = {k: v for k, v in sorted(objc.items()) if v}
        rec = {"binary": r, "libraries": libs, "rpaths": rp,
               "install_name": self_name, "ordinal_table": table,
               "imports": imp_out, "objc_classes": objc,
               "selrefs": selrefs(b), "methtypes": sorted(set(cstrings(section(b, "__TEXT", "__objc_methtype"))))}
        (OUT / f"{safe_name(r)}.json").write_text(json.dumps(rec, indent=1))
        with open(OUT / f"{safe_name(r)}.imports.tsv", "w") as f:
            f.write("library\tsymbol\tweak\tlazy\n")
            for lib, lst in imp_out.items():
                for e in lst:
                    f.write(f"{lib}\t{e['name']}\t{int(e['weak'])}\t{int(e['lazy'])}\n")
        summary[r] = {"libraries": len(libs), "imported_symbols": sum(len(v) for v in imp_out.values()),
                      "objc_class_imports": sum(len(v) for v in objc.values()), "selrefs": len(rec["selrefs"])}
        print(r, summary[r])
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
