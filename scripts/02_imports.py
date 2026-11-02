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


def main():
    app = app_dir()
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {}
    for p in find_machos(app):
        r = rel(p, app)
        b = parse(p)[0]
        libs = [{"name": l.name, "weak": "WEAK" in str(l.command)} for l in b.libraries
                if "ID_DYLIB" not in str(l.command)]
        imports = defaultdict(dict)
        for bi in b.bindings:
            if not bi.has_symbol:
                continue
            name = bi.symbol.name
            lib = bi.library.name if bi.has_library else f"<ordinal {getattr(bi, 'library_ordinal', '?')}>"
            weak = bool(getattr(bi, "weak_import", False))
            prev = imports[lib].get(name)
            imports[lib][name] = weak if prev is None else (prev and weak)
        # symbols imported without a binding (e.g. only lazy / unused) still matter for resolution
        for s in b.imported_symbols:
            if s.has_binding_info:
                continue
            lib = s.library.name if getattr(s, "library", None) else f"<ordinal {s.library_ordinal}>"
            imports[lib].setdefault(s.name, False)
        imp_out = {lib: [{"name": n, "weak": w} for n, w in sorted(d.items())] for lib, d in sorted(imports.items())}
        objc = {lib: sorted({n.split("$_", 1)[1] for n in d if n.startswith(("_OBJC_CLASS_$_", "_OBJC_METACLASS_$_"))})
                for lib, d in imports.items()}
        objc = {k: v for k, v in sorted(objc.items()) if v}
        rec = {"binary": r, "libraries": libs, "imports": imp_out, "objc_classes": objc,
               "selrefs": selrefs(b), "methtypes": sorted(set(cstrings(section(b, "__TEXT", "__objc_methtype"))))}
        (OUT / f"{safe_name(r)}.json").write_text(json.dumps(rec, indent=1))
        with open(OUT / f"{safe_name(r)}.imports.tsv", "w") as f:
            f.write("library\tsymbol\tweak\n")
            for lib, lst in imp_out.items():
                for e in lst:
                    f.write(f"{lib}\t{e['name']}\t{int(e['weak'])}\n")
        summary[r] = {"libraries": len(libs), "imported_symbols": sum(len(v) for v in imp_out.values()),
                      "objc_class_imports": sum(len(v) for v in objc.values()), "selrefs": len(rec["selrefs"])}
        print(r, summary[r])
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
