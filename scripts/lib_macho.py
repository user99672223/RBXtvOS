"""Shared helpers for Mach-O inspection (LIEF based). Pure analysis, no writes."""
import plistlib
import struct
from pathlib import Path

import lief

lief.logging.disable()

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
UNPACKED = WORK / "unpacked"
EVIDENCE = ROOT / "evidence"

PLATFORMS = {1: "macOS", 2: "iOS", 3: "tvOS", 4: "watchOS", 5: "bridgeOS", 6: "macCatalyst",
             7: "iOSSimulator", 8: "tvOSSimulator", 9: "watchOSSimulator", 10: "driverKit",
             11: "visionOS", 12: "visionOSSimulator"}


def app_dir(base=UNPACKED):
    apps = sorted((base / "Payload").glob("*.app"))
    assert len(apps) == 1, apps
    return apps[0]


def is_macho(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            m = f.read(4)
    except OSError:
        return False
    return m in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")


def find_machos(app: Path):
    out = []
    for p in sorted(app.rglob("*")):
        if p.is_file() and not p.is_symlink() and is_macho(p):
            out.append(p)
    return out


def ver(v: int) -> str:
    return f"{v >> 16}.{(v >> 8) & 0xff}.{v & 0xff}"


def parse(path: Path):
    fat = lief.MachO.parse(str(path))
    return [fat.at(i) for i in range(fat.size)]


def build_version(b):
    for c in b.commands:
        if c.command == lief.MachO.LoadCommand.TYPE.BUILD_VERSION:
            plat = int(c.platform)
            return {"platform": PLATFORMS.get(plat, str(plat)), "platform_id": plat,
                    "minos": ".".join(map(str, c.minos)), "sdk": ".".join(map(str, c.sdk)),
                    "tools": [str(t.tool).split(".")[-1] + " " + ".".join(map(str, t.version)) for t in c.tools]}
        if c.command in (lief.MachO.LoadCommand.TYPE.VERSION_MIN_IPHONEOS,
                         lief.MachO.LoadCommand.TYPE.VERSION_MIN_TVOS):
            return {"platform": str(c.command), "minos": ".".join(map(str, c.version)),
                    "sdk": ".".join(map(str, c.sdk))}
    return None


def encryption(b):
    for c in b.commands:
        if c.command in (lief.MachO.LoadCommand.TYPE.ENCRYPTION_INFO,
                         lief.MachO.LoadCommand.TYPE.ENCRYPTION_INFO_64):
            return int(c.crypt_id)
    return None


def libraries(b):
    out = []
    for lib in b.libraries:
        kind = str(lib.command).split(".")[-1]
        out.append({"name": lib.name, "kind": kind,
                    "current": ".".join(map(str, lib.current_version)),
                    "compat": ".".join(map(str, lib.compatibility_version))})
    return out


def entitlements(path: Path):
    """Extract the entitlements plist from the embedded code signature, if any."""
    b = parse(path)[0]
    if not b.has_code_signature:
        return None
    cs = b.code_signature
    data = bytes(cs.content)
    if len(data) < 12:
        return None
    magic, length, count = struct.unpack(">III", data[:12])
    if magic != 0xFADE0CC0:
        return None
    for i in range(count):
        typ, off = struct.unpack(">II", data[12 + 8 * i: 20 + 8 * i])
        bmagic, blen = struct.unpack(">II", data[off: off + 8])
        if bmagic == 0xFADE7171:  # CSMAGIC_EMBEDDED_ENTITLEMENTS
            return plistlib.loads(data[off + 8: off + blen])
    return {}


def rel(p: Path, app: Path) -> str:
    return str(p.relative_to(app))


# --------------------------------------------------------------- export trie
#
# What a dylib exports is the dyld export trie, not the symbol table. LIEF's
# `exported_symbols` returned only 248 names for Persona2, whose trie is 114 KB
# and holds well over a thousand Swift symbols; the symbols Roblox imports from
# it appear in Persona2's symtab as UNDEFINED, so the symtab cannot answer the
# question either. Parsing the trie directly is what makes "does this bundled
# framework export what the main binary imports" answerable.
#
# Node layout (dyld, ExportsTrie):
#   terminal_size : ULEB128
#   if terminal_size: flags : ULEB128
#       REEXPORT            -> ordinal : ULEB128, imported_name : cstring
#       STUB_AND_RESOLVER   -> stub : ULEB128, resolver : ULEB128
#       otherwise           -> address : ULEB128
#   child_count : uint8
#   child_count x { edge : cstring, node_offset : ULEB128 }

EXPORT_SYMBOL_FLAGS_KIND_MASK = 0x03
EXPORT_SYMBOL_FLAGS_REEXPORT = 0x08
EXPORT_SYMBOL_FLAGS_STUB_AND_RESOLVER = 0x10


def _uleb(data: bytes, pos: int):
    result, shift = 0, 0
    while True:
        if pos >= len(data):
            raise ValueError("truncated ULEB128 in export trie")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("oversized ULEB128 in export trie")


def _cstr(data: bytes, pos: int):
    end = data.index(b"\0", pos)
    return data[pos:end].decode("utf-8", "replace"), end + 1


def parse_export_trie(data: bytes):
    """Walk an export trie blob. Returns {name: {...}}."""
    out = {}
    if not data:
        return out
    # (node offset, accumulated prefix); guard against malformed cycles
    stack = [(0, "")]
    seen = set()
    while stack:
        offset, prefix = stack.pop()
        if offset in seen and prefix == "":
            continue
        if offset >= len(data):
            continue
        key = (offset, prefix)
        if key in seen:
            continue
        seen.add(key)
        pos = offset
        terminal_size, pos = _uleb(data, pos)
        if terminal_size:
            end_of_terminal = pos + terminal_size
            flags, p2 = _uleb(data, pos)
            info = {"flags": flags, "kind": flags & EXPORT_SYMBOL_FLAGS_KIND_MASK}
            if flags & EXPORT_SYMBOL_FLAGS_REEXPORT:
                ordinal, p2 = _uleb(data, p2)
                imported, p2 = _cstr(data, p2)
                info.update(reexport_ordinal=ordinal, imported_name=imported or prefix)
            elif flags & EXPORT_SYMBOL_FLAGS_STUB_AND_RESOLVER:
                stub, p2 = _uleb(data, p2)
                resolver, p2 = _uleb(data, p2)
                info.update(stub=stub, resolver=resolver)
            else:
                address, p2 = _uleb(data, p2)
                info.update(address=address)
            if prefix:
                out[prefix] = info
            pos = end_of_terminal
        if pos >= len(data):
            continue
        child_count = data[pos]
        pos += 1
        for _ in range(child_count):
            try:
                edge, pos = _cstr(data, pos)
                child_offset, pos = _uleb(data, pos)
            except (ValueError, IndexError):
                break
            stack.append((child_offset, prefix + edge))
    return out


def export_trie_blob(b):
    """The raw export-trie bytes of a parsed Mach-O, from either load command."""
    for c in b.commands:
        if c.command == lief.MachO.LoadCommand.TYPE.DYLD_EXPORTS_TRIE:
            try:
                return bytes(c.content)
            except Exception:
                pass
    di = getattr(b, "dyld_info", None)
    if di is not None:
        try:
            off, size = di.export_info
        except Exception:
            off = size = 0
        if size:
            raw = getattr(b, "original_bytes", None)
            if raw is None:
                return None, (off, size)
            return bytes(raw[off:off + size])
    return None


def exported_names(b, path=None):
    """Every name this Mach-O exports, read from the export trie with the
    symbol table as a fallback. Returns (names, source)."""
    blob = None
    for c in b.commands:
        if c.command == lief.MachO.LoadCommand.TYPE.DYLD_EXPORTS_TRIE:
            try:
                blob = bytes(c.content)
            except Exception:
                blob = None
            break
    if blob is None:
        di = getattr(b, "dyld_info", None)
        off = size = 0
        if di is not None:
            try:
                off, size = di.export_info
            except Exception:
                off = size = 0
        if size and path is not None:
            with open(path, "rb") as f:
                # offsets in a thin slice are file-relative for a non-fat file;
                # for a fat file LIEF hands us the slice, so seek within it
                f.seek(off)
                blob = f.read(size)
    if blob:
        try:
            trie = parse_export_trie(blob)
            if trie:
                return set(trie.keys()), "export_trie"
        except Exception:
            pass
    names = {s.name for s in b.exported_symbols if s.name}
    return names, "lief_exported_symbols"
