//
//  tvlog.h — on-device logging for the Roblox tvOS port (CLAUDE.md Phase 7)
//
//  Two channels, always both:
//
//    os_log   subsystem "tvport", one category per concern. This is the
//             primary channel and is what `pymobiledevice3 syslog live` shows,
//             filterable by subsystem without the app being attached to a
//             debugger.
//    file     a mirrored, rotated text log in the app's Caches directory, for
//             when nothing is listening on syslog. tvOS may purge Caches, so
//             the file is a convenience and os_log is the record.
//
//  Nothing here ever swallows a crash: the exception and signal handlers write
//  a final record and then chain to whatever was installed before us.
//
#ifndef TVLOG_H
#define TVLOG_H

#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/// Categories. Kept in sync with tvlog_category_name() in tvlog.m.
typedef enum {
    TVLogShim = 0,
    TVLogCursor,
    TVLogInput,
    TVLogLifecycle,
    TVLogStorage,
    TVLogCrash,
    TVLogCategoryCount
} tvlog_category_t;

/// Levels. The active level is read from the Info.plist key `TVPortLogLevel`
/// ("off", "error", "warn", "info", "debug", "verbose"). Phase 7 ships
/// "verbose" as the default for this development phase.
typedef enum {
    TVLogLevelOff = 0,
    TVLogLevelError,
    TVLogLevelWarn,
    TVLogLevelInfo,
    TVLogLevelDebug,
    TVLogLevelVerbose
} tvlog_level_t;

/// Installed from a library constructor; safe to call again (idempotent).
void tvlog_init(void);

/// True when a message at this level would be emitted. Use it to skip
/// expensive argument formatting.
bool tvlog_enabled(tvlog_level_t level);

/// The active level, after Info.plist is consulted.
tvlog_level_t tvlog_level(void);

void tvlog_write(tvlog_category_t category, tvlog_level_t level,
                 const char *fmt, ...) __attribute__((format(printf, 3, 4)));
void tvlog_vwrite(tvlog_category_t category, tvlog_level_t level,
                  const char *fmt, va_list ap);

#define TVLOG_E(cat, ...) tvlog_write((cat), TVLogLevelError,   __VA_ARGS__)
#define TVLOG_W(cat, ...) tvlog_write((cat), TVLogLevelWarn,    __VA_ARGS__)
#define TVLOG_I(cat, ...) tvlog_write((cat), TVLogLevelInfo,    __VA_ARGS__)
#define TVLOG_D(cat, ...) tvlog_write((cat), TVLogLevelDebug,   __VA_ARGS__)
#define TVLOG_V(cat, ...) tvlog_write((cat), TVLogLevelVerbose, __VA_ARGS__)

// ---------------------------------------------------------------- shim counters
//
// Every shim logs its *first* call with a short caller backtrace, then only
// counts later calls. Counts are written periodically, at exit, and as part of
// the crash record, so a shim that is being hammered shows up without the log
// turning into noise.

typedef struct tvlog_shim_counter {
    const char *name;                  // "UIDatePicker -setDate:animated:"
    const char *decision;              // "STUB" / "REIMPLEMENT" / "REDIRECT"
    _Atomic uint64_t calls;
    _Atomic bool first_logged;
    struct tvlog_shim_counter *next;   // intrusive registry list
    _Atomic bool registered;
} tvlog_shim_counter_t;

/// Records a call against `counter`, logging the first one with a backtrace.
void tvlog_shim_enter(tvlog_shim_counter_t *counter);

/// Declare-and-record in one line, at the top of a shim implementation.
#define TVLOG_SHIM(nameLiteral, decisionLiteral)                               \
    do {                                                                       \
        static tvlog_shim_counter_t _tvlog_counter = {                         \
            .name = (nameLiteral), .decision = (decisionLiteral)               \
        };                                                                     \
        tvlog_shim_enter(&_tvlog_counter);                                      \
    } while (0)

/// Write the counter table now (also called on a timer, at exit, and on crash).
void tvlog_flush_counters(const char *reason);

// ------------------------------------------------------------------- bookkeeping

/// Called by each shim library's constructor so the startup banner can list
/// which shims are actually loaded. `name` must outlive the process.
void tvlog_register_shim_library(const char *name);

/// Writes the startup banner: build id, Roblox version, tvOS version, device
/// model and the loaded shim libraries. Called once, after the first run loop
/// turn, so late-loading shim libraries are included.
void tvlog_banner(void);

/// Absolute path of the mirrored log file, or NULL if it could not be opened.
const char *tvlog_file_path(void);

#ifdef __cplusplus
}
#endif

#endif /* TVLOG_H */
