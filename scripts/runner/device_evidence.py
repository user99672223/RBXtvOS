#!/usr/bin/env python3
"""Evidence source 1: the device's own system libraries.

Runs on the macOS Actions runner against a dyld shared cache extracted from the
AppleTV14,1 OTA. Produces derived text only (install names, export lists,
re-export edges); the cache itself never leaves the runner.

What "missing on the device" means here is exactly what dyld will decide at
load time. Roblox is two-level-namespace bound: an import names both a symbol
and the library it must come from, and dyld resolves it against that library's
exports plus the exports of the libraries that library *re-exports*. So for
each system library any Roblox Mach-O links, this records:

  * whether the cache contains it at all (missing framework), and
  * its export set, and the transitive closure over re-exports (missing symbol).

`ipsw dyld tbd` is used to read exports, so the output is in the same .tbd
shape the SDK uses and is parsed by the same reader (scripts/runner/tbd.py).

usage: device_evidence.py <dyld_shared_cache> <label>
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tbd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "evidence"
QUERY = EVIDENCE / "queries" / "sdk_query.json"

IPSW = shutil.which("ipsw") or "ipsw"


def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def cache_header(cache: Path) -> str:
    r = run([IPSW, "dyld", "info", str(cache)])
    return r.stdout or r.stderr


def list_images(cache: Path):
    """All install names in the cache, with versions when ipsw reports them."""
    r = run([IPSW, "dyld", "info", "--dylibs", "--json", str(cache)])
    if r.returncode == 0 and r.stdout.strip():
        try:
            doc = json.loads(r.stdout)
        except json.JSONDecodeError:
            doc = None
        if doc is not None:
            names = []
            # ipsw has used a few shapes here; accept the common ones.
            if isinstance(doc, dict):
                seq = doc.get("dylibs") or doc.get("images") or []
            else:
                seq = doc
            for e in seq:
                if isinstance(e, str):
                    names.append(e)
                elif isinstance(e, dict):
                    n = e.get("name") or e.get("path") or e.get("install_name")
                    if n:
                        names.append(n)
            if names:
                return sorted(set(names))
    # text fallback: lines contain the install path
    r = run([IPSW, "dyld", "info", "--dylibs", str(cache)])
    names = []
    for line in r.stdout.splitlines():
        for tok in line.split():
            if tok.startswith("/") and ("." in tok):
                names.append(tok)
                break
    return sorted(set(names))


def tbd_for(cache: Path, install_name: str, workdir: Path):
    """Generate and parse a .tbd for one in-cache dylib."""
    before = set(workdir.rglob("*.tbd"))
    r = run([IPSW, "dyld", "tbd", str(cache), install_name, "-o", str(workdir)])
    after = set(workdir.rglob("*.tbd"))
    new = sorted(after - before)
    if not new:
        # ipsw may overwrite an existing file with the same name
        guess = workdir / (Path(install_name).name + ".tbd")
        if guess.is_file():
            new = [guess]
    if not new:
        return None, (r.stderr or r.stdout or "no .tbd produced").strip()[:400]
    try:
        return tbd.read(new[0]), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cache, label = Path(sys.argv[1]), sys.argv[2]
    assert cache.is_file(), f"no such cache: {cache}"
    q = json.loads(QUERY.read_text())

    out_dir = EVIDENCE / "exports" / f"device_{label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    sdk_dir = EVIDENCE / "sdk"
    sdk_dir.mkdir(parents=True, exist_ok=True)

    (sdk_dir / f"device_{label}_cache_header.txt").write_text(cache_header(cache))

    images = list_images(cache)
    (out_dir / "_images.txt").write_text("\n".join(images) + "\n")
    print(f"cache holds {len(images)} images")
    image_set = set(images)

    # Resolve the queried libraries, then follow re-exports transitively so the
    # closure dyld would search is fully recorded.
    wanted = list(q["libraries"])
    done, results, errors = set(), {}, {}
    workdir = Path(tempfile.mkdtemp(prefix="tbd-"))
    queue = list(wanted)
    while queue:
        name = queue.pop(0)
        if name in done:
            continue
        done.add(name)
        if name not in image_set:
            results[name] = {"present_in_cache": False}
            continue
        info, err = tbd_for(cache, name, workdir)
        if info is None:
            errors[name] = err
            results[name] = {"present_in_cache": True, "error": err}
            print(f"  !! {name}: {err}")
            continue
        names = sorted(tbd.linker_names(info))
        safe = name.lstrip("/").replace("/", "__")
        (out_dir / f"{safe}.txt").write_text("\n".join(names) + "\n")
        results[name] = {
            "present_in_cache": True,
            "exports": len(names),
            "reexports": info["reexports"],
            "install_names": info["install_names"],
            "targets": info["targets"],
            "queried": name in wanted,
        }
        for r in info["reexports"]:
            if r not in done:
                queue.append(r)
    shutil.rmtree(workdir, ignore_errors=True)

    (sdk_dir / f"device_{label}_libraries.json").write_text(json.dumps(results, indent=1))
    present = sum(1 for v in results.values() if v.get("present_in_cache"))
    absent = [k for k, v in results.items() if not v.get("present_in_cache")]
    print(f"resolved {len(results)} libraries ({present} present, {len(absent)} absent)")
    if absent:
        print("absent from the device cache:")
        for a in sorted(absent):
            print("   ", a)
    if errors:
        print(f"{len(errors)} libraries could not be stubbed; see the JSON")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
