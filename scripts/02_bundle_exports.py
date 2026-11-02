#!/usr/bin/env python3
"""Phase 2: what each Roblox Mach-O itself exports.

Needed to resolve imports that name a bundled framework (`@rpath/...`) rather
than a system library: those must resolve inside the bundle, not against the
device. Written to evidence/exports/bundle/<binary>.txt in the same
one-name-per-line shape as the device and SDK export lists, so the resolution
check in scripts/02_missing.py treats all three sources identically.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_macho import EVIDENCE, app_dir, find_machos, parse, rel  # noqa: E402

OUT = EVIDENCE / "exports" / "bundle"


def main():
    app = app_dir()
    OUT.mkdir(parents=True, exist_ok=True)
    index = {}
    for p in find_machos(app):
        r = rel(p, app)
        b = parse(p)[0]
        names = set()
        for s in b.exported_symbols:
            if s.name:
                names.add(s.name)
        # LC_ID_DYLIB install name is how other binaries in the bundle name it
        install = None
        for lib in b.libraries:
            if "ID_DYLIB" in str(lib.command):
                install = lib.name
                break
        safe = r.replace("/", "__")
        (OUT / f"{safe}.txt").write_text("\n".join(sorted(names)) + "\n")
        index[r] = {"install_name": install, "exports": len(names),
                    "file": f"{safe}.txt"}
        print(f"{r}: {len(names)} exports  install_name={install}")
    (OUT / "_index.json").write_text(json.dumps(index, indent=1))


if __name__ == "__main__":
    main()
