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
