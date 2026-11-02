#!/usr/bin/env python3
"""Minimal compiled asset catalog (Assets.car / BOMStore) reader.
Reports the CAR header (platform/deployment info), the key format and a histogram of
rendition attribute values, so we can see which idioms (universal/phone/pad/tv) a catalog targets.
Usage: carinfo.py <Assets.car> [--json]"""
import collections
import json
import struct
import sys

# CoreUI rendition attribute identifiers (RenditionAttributeType, Look = 0).
ATTR = {0: "look", 1: "element", 2: "part", 3: "size", 4: "direction", 5: "placeholder", 6: "value",
        7: "appearance", 8: "dimension1", 9: "dimension2", 10: "state", 11: "layer", 12: "scale",
        13: "localization", 14: "presentationState", 15: "idiom", 16: "subtype", 17: "identifier",
        18: "previousValue", 19: "previousState", 20: "sizeClassH", 21: "sizeClassV", 22: "memoryClass",
        23: "graphicsClass", 24: "displayGamut", 25: "deploymentTarget", 26: "glyphWeight", 27: "glyphSize"}
IDIOM = {0: "universal", 1: "phone", 2: "pad", 3: "tv", 4: "car", 5: "watch", 6: "marketing", 7: "mac",
         8: "vision"}


class Bom:
    def __init__(self, data):
        self.d = data
        assert data[:8] == b"BOMStore", "not a BOM file"
        _, nblocks, idx_off, idx_len, vars_off, vars_len = struct.unpack_from(">6I", data, 8)
        cnt = struct.unpack_from(">I", data, idx_off)[0]
        self.blocks = [struct.unpack_from(">II", data, idx_off + 4 + 8 * i) for i in range(cnt)]
        self.vars = {}
        n = struct.unpack_from(">I", data, vars_off)[0]
        p = vars_off + 4
        for _ in range(n):
            bi, ln = struct.unpack_from(">IB", data, p)
            self.vars[data[p + 5:p + 5 + ln].decode()] = bi
            p += 5 + ln

    def block(self, i):
        a, ln = self.blocks[i]
        return self.d[a:a + ln]

    def named(self, name):
        return self.block(self.vars[name]) if name in self.vars else None

    def tree(self, name):
        t = self.named(name)
        if t is None or t[:4] != b"tree":
            return []
        _, child, _bs, _pc = struct.unpack_from(">IIII", t, 4)
        out = []

        def walk(bi):
            b = self.block(bi)
            is_leaf, count, fwd, _bwd = struct.unpack_from(">HHII", b, 0)
            if not is_leaf:
                val, _ = struct.unpack_from(">II", b, 12)
                walk(val)
                return
            for i in range(count):
                val, key = struct.unpack_from(">II", b, 12 + 8 * i)
                out.append((self.block(key), self.block(val)))
            if fwd:
                walk(fwd)
        walk(child)
        return out


def info(path):
    bom = Bom(open(path, "rb").read())
    res = {"vars": sorted(bom.vars)}
    hdr = bom.named("CARHEADER")
    if hdr:
        # 'RATC' tag, coreUIVersion, storageVersion, storageTimestamp, renditionCount, mainVersion[128], versionString[256]
        tag, cui, sv, ts, rc = struct.unpack_from("<4sIIII", hdr, 0)
        main = hdr[20:148].split(b"\0")[0].decode(errors="replace")
        vs = hdr[148:404].split(b"\0")[0].decode(errors="replace")
        res["header"] = {"coreUIVersion": cui, "storageVersion": sv, "renditionCount": rc,
                         "mainVersion": main, "versionString": vs}
    meta = bom.named("EXTENDED_METADATA")
    if meta:
        # 'META' tag then thinningArguments[256], deploymentPlatformVersion[256], deploymentPlatform[256], authoringTool[256]
        f = [meta[4 + 256 * i: 4 + 256 * (i + 1)].split(b"\0")[0].decode(errors="replace") for i in range(4)]
        res["extended_metadata"] = dict(zip(["thinningArguments", "deploymentPlatformVersion",
                                             "deploymentPlatform", "authoringTool"], f))
    kf = bom.named("KEYFORMAT")
    attrs = []
    if kf:
        _tag, _ver, n = struct.unpack_from("<4sII", kf, 0)
        attrs = list(struct.unpack_from(f"<{n}I", kf, 12))
    res["key_attributes"] = [ATTR.get(a, str(a)) for a in attrs]
    hist = collections.defaultdict(collections.Counter)
    for key, _val in bom.tree("RENDITIONS"):
        vals = struct.unpack_from(f"<{len(key)//2}H", key)
        for a, v in zip(attrs, vals):
            hist[ATTR.get(a, str(a))][v] += 1
    res["histogram"] = {k: dict(sorted(v.items())) for k, v in hist.items()}
    if "idiom" in hist:
        res["idioms"] = {IDIOM.get(k, str(k)): v for k, v in sorted(hist["idiom"].items())}
    res["facets"] = len(bom.tree("FACETKEYS"))
    return res


if __name__ == "__main__":
    r = info(sys.argv[1])
    print(json.dumps(r, indent=1))
