//
//  shimsupport.h — shared scaffolding for the Phase 4 shim libraries
//
//  Two kinds of shim, per CLAUDE.md Phase 4:
//
//    proxy    the framework exists on the device but does not export some
//             symbol Roblox imports. The shim re-exports the real framework
//             (so every other symbol still resolves through it) and defines
//             only what is missing.
//    standin  the framework does not exist on the device at all. The shim
//             defines only the symbols Roblox imports from it, nothing else.
//
//  Every shim registers itself with tvlog on load, so the startup banner lists
//  what is actually in the process, and every entry point logs its first call
//  with a caller backtrace and is counted thereafter.
//
#ifndef TVPORT_SHIMSUPPORT_H
#define TVPORT_SHIMSUPPORT_H

#include "../tvlog/tvlog.h"

/// Name this shim library in the startup banner.
#define TVPORT_SHIM_LIBRARY(nameLiteral)                                       \
    __attribute__((constructor)) static void tvport_shim_register(void) {      \
        tvlog_register_shim_library(nameLiteral);                              \
    }

/// Record a call against a shim entry point. `decision` is the Phase 3 verdict
/// so the log says what was promised as well as what ran.
#define TVPORT_SHIM_CALL(nameLiteral, decisionLiteral)                         \
    TVLOG_SHIM(nameLiteral, decisionLiteral)

/// Define a data symbol under an exact Mach-O symbol name.
///
/// The names involved cannot be written as plain C identifiers: Swift's
/// autolink markers contain '$' (__swift_FORCE_LOAD_$_swiftCallKit) and the
/// leading underscore the compiler normally adds would be wrong. An explicit
/// asm label sets the symbol name verbatim, so the string here is exactly what
/// appears in `nm` output, including its leading underscore.
#define TVPORT_DEFINE_DATA(cIdentifier, machoName, type, initialiser)          \
    type cIdentifier __asm__(machoName) = initialiser

/// A Swift autolink marker. Swift emits an undefined reference to one of these
/// per imported framework purely to force the linker to pull the overlay in;
/// nothing ever reads the value, so a zero word is a faithful stand-in.
#define TVPORT_SWIFT_FORCE_LOAD(cIdentifier, machoName)                        \
    const void *cIdentifier __asm__(machoName) = 0

#endif /* TVPORT_SHIMSUPPORT_H */
