"""Minimal NIBArchive reader: returns the class names a compiled .nib instantiates,
plus class-name strings that appear as values (e.g. UIClassName / custom classes)."""
import struct


def _varint(b, i):
    r = s = 0
    while True:
        c = b[i]; i += 1
        r |= (c & 0x7F) << s
        if c & 0x80:
            return r, i
        s += 7


def read(path):
    b = open(path, "rb").read()
    if not b.startswith(b"NIBArchive"):
        return None
    (_, _, n_obj, o_obj, n_key, o_key, n_val, o_val, n_cls, o_cls) = struct.unpack_from("<10I", b, 10)
    classes = []
    i = o_cls
    for _ in range(n_cls):
        ln, i = _varint(b, i)
        nextra, i = _varint(b, i)
        i += 4 * nextra
        classes.append(b[i:i + ln].rstrip(b"\0").decode())
        i += ln
    keys = []
    i = o_key
    for _ in range(n_key):
        ln, i = _varint(b, i)
        keys.append(b[i:i + ln].decode(errors="replace"))
        i += ln
    # values: key idx varint, type byte, payload
    strings = []
    i = o_val
    for _ in range(n_val):
        k, i = _varint(b, i)
        t = b[i]; i += 1
        if t in (0,):
            i += 1
        elif t == 1:
            i += 2
        elif t == 2:
            i += 4
        elif t == 3:
            i += 8
        elif t in (4, 5, 9):
            pass
        elif t == 6:
            i += 4
        elif t == 7:
            i += 8
        elif t == 8:
            ln, i = _varint(b, i)
            strings.append((keys[k], b[i:i + ln]))
            i += ln
        elif t == 10:
            i += 4
        else:
            break
    obj_classes = []
    i = o_obj
    for _ in range(n_obj):
        ci, i = _varint(b, i)
        _, i = _varint(b, i)
        _, i = _varint(b, i)
        obj_classes.append(classes[ci])
    custom = sorted({v.decode(errors="replace") for k, v in strings
                     if k in ("UIClassName", "UIOriginalClassName", "UICustomClassName")})
    return {"classes": sorted(set(obj_classes)), "custom_classes": custom,
            "keys": sorted(set(keys))}
