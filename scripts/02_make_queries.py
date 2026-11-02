#!/usr/bin/env python3
"""Phase 2: build the query files the Actions runners consume (derived Roblox metadata only).

evidence/queries/libraries.txt   every system library any Roblox Mach-O links (absolute paths)
evidence/queries/sdk_query.json  {libraries, classes{lib:[cls]}, symbols{lib:[sym]}, selectors[]}
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib_macho import EVIDENCE  # noqa: E402

REMOVED_EXTENSIONS = ("PlugIns/",)  # extensions are removed in Phase 1/5; still queried for the record


def main():
    libs = set()
    classes = defaultdict(set)
    symbols = defaultdict(set)
    selectors = set()
    for f in sorted((EVIDENCE / "imports").glob("*.json")):
        if f.name == "summary.json":
            continue
        d = json.loads(f.read_text())
        for l in d["libraries"]:
            if l["name"].startswith("/"):
                libs.add(l["name"])
        for lib, lst in d["imports"].items():
            if not lib.startswith("/"):
                continue
            for e in lst:
                n = e["name"]
                if n.startswith(("_OBJC_CLASS_$_", "_OBJC_METACLASS_$_")):
                    classes[lib].add(n.split("$_", 1)[1])
                else:
                    symbols[lib].add(n)
        selectors.update(d["selrefs"])
    q = EVIDENCE / "queries"
    q.mkdir(exist_ok=True)
    (q / "libraries.txt").write_text("\n".join(sorted(libs)) + "\n")
    (q / "sdk_query.json").write_text(json.dumps({
        "libraries": sorted(libs),
        "classes": {k: sorted(v) for k, v in sorted(classes.items())},
        "symbols": {k: sorted(v) for k, v in sorted(symbols.items())},
        "selectors": sorted(selectors),
    }, indent=1))
    print(f"{len(libs)} libraries, {sum(map(len, classes.values()))} classes, "
          f"{sum(map(len, symbols.values()))} symbols, {len(selectors)} selectors")


if __name__ == "__main__":
    main()
