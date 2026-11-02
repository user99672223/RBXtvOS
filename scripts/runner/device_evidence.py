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
import re
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


def run_device_queries(cache: Path, label: str):
    """Run arbitrary `ipsw` queries against the cache while the runner has it.

    This is the `evidence-query` mechanism CLAUDE.md asks for, folded into the
    job that already holds the extracted cache: re-extracting it for each
    question would cost another multi-GB download and decompression.

    Driven by evidence/queries/device_queries.json:

      {"queries": [
         {"id": "uitouch-ivars", "why": "...",
          "args": ["dyld", "macho", "<CACHE>",
                   "/System/Library/PrivateFrameworks/UIKitCore.framework/UIKitCore",
                   "--objc"],
          "grep": "UITouch"}
      ]}

    "<CACHE>" is replaced with the cache path. `grep` optionally narrows huge
    outputs (a full UIKitCore class dump is enormous) to the lines that matter
    plus context. Output is derived text about Apple's own system libraries,
    which CLAUDE.md permits; the cache itself never leaves the runner.
    """
    spec_path = ROOT / "evidence" / "queries" / "device_queries.json"
    if not spec_path.is_file():
        print("no device_queries.json; skipping targeted cache queries")
        return
    spec = json.loads(spec_path.read_text())
    out_dir = EVIDENCE / "device_queries"
    out_dir.mkdir(parents=True, exist_ok=True)
    index = {}
    for q in spec.get("queries", []):
        qid = q.get("id")
        args = q.get("args")
        if not qid or not args:
            continue
        argv = [IPSW] + [str(cache) if a == "<CACHE>" else a for a in args]
        timeout = int(q.get("timeout", 1800))
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            text = r.stdout or ""
            err = (r.stderr or "").strip()
            rc = r.returncode
        except subprocess.TimeoutExpired:
            text, err, rc = "", f"timed out after {timeout}s", -1

        pattern = q.get("grep")
        if pattern and text:
            ctx = int(q.get("grep_context", 4))
            keep, lines = [], text.splitlines()
            rx = re.compile(pattern)
            hit_lines = [i for i, l in enumerate(lines) if rx.search(l)]
            shown = set()
            for i in hit_lines:
                for j in range(max(0, i - ctx), min(len(lines), i + ctx + 1)):
                    shown.add(j)
            for j in sorted(shown):
                keep.append(lines[j])
            text = "\n".join(keep)
            matched = len(hit_lines)
        else:
            matched = None

        limit = int(q.get("max_bytes", 400_000))
        truncated = len(text) > limit
        if truncated:
            text = text[:limit] + "\n...(truncated)\n"

        header = (f"# device query {qid}\n"
                  f"# why: {q.get('why','')}\n"
                  f"# argv: {' '.join(argv)}\n"
                  f"# label: {label}\n"
                  f"# returncode: {rc}\n")
        if pattern:
            header += f"# grep: {pattern}  (matching lines: {matched})\n"
        if err:
            header += "# stderr: " + err.replace("\n", " ")[:600] + "\n"
        (out_dir / f"{qid}.txt").write_text(header + "\n" + text)
        index[qid] = {"why": q.get("why", ""), "argv": argv[1:], "returncode": rc,
                      "grep": pattern, "matching_lines": matched,
                      "truncated": truncated, "bytes": len(text)}
        print(f"  query {qid}: rc={rc} bytes={len(text)} matches={matched}")
    (out_dir / "_index.json").write_text(json.dumps(index, indent=1))


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

    run_device_queries(cache, label)
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
