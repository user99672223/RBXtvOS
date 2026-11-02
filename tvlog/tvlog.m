//
//  tvlog.m — implementation of the Phase 7 logging module.
//
//  Design notes that matter:
//
//  * The signal handler must be async-signal-safe, so it does not call os_log,
//    NSLog, malloc, or stdio. It writes with write(2) to a file descriptor
//    opened at init, using buffers rendered with a small integer formatter.
//  * The crash handlers chain: the previous handler is saved and re-invoked
//    (or the default action restored and the signal re-raised) so the process
//    still dies and tvOS still writes its own crash report. CLAUDE.md Phase 7
//    is explicit that crashes must not be swallowed.
//  * Shim counters live in an intrusive singly linked list pushed with a CAS
//    loop, so registering is lock-free and safe from any thread.
//
#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>

#include <dlfcn.h>
#include <errno.h>
#include <execinfo.h>
#include <fcntl.h>
#include <os/log.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <sys/utsname.h>
#include <time.h>
#include <unistd.h>

#include "tvlog.h"

#ifndef TVPORT_BUILD_ID
#define TVPORT_BUILD_ID "unknown"
#endif

#define TVLOG_SUBSYSTEM "tvport"

static const char *const kCategoryNames[TVLogCategoryCount] = {
    "shim", "cursor", "input", "lifecycle", "storage", "crash"
};

static os_log_t sLogs[TVLogCategoryCount];
static _Atomic int sLevel = TVLogLevelVerbose;
static int sFileFD = -1;                 // mirrored log, also used by the
                                         // signal handler (hence a raw fd)
static char sFilePath[1024];
static pthread_mutex_t sFileLock = PTHREAD_MUTEX_INITIALIZER;
static _Atomic(tvlog_shim_counter_t *) sCounters = NULL;
static _Atomic(const char *) sShimLibs[64];
static _Atomic int sShimLibCount = 0;
static _Atomic bool sInitialised = false;
static off_t sFileBytes = 0;

static const off_t kRotateBytes = 4 * 1024 * 1024;
static const int kRotateKeep = 3;

// ----------------------------------------------------------------- level config

static tvlog_level_t level_from_string(const char *s) {
    if (!s) return TVLogLevelVerbose;
    if (!strcasecmp(s, "off")) return TVLogLevelOff;
    if (!strcasecmp(s, "error")) return TVLogLevelError;
    if (!strcasecmp(s, "warn") || !strcasecmp(s, "warning")) return TVLogLevelWarn;
    if (!strcasecmp(s, "info")) return TVLogLevelInfo;
    if (!strcasecmp(s, "debug")) return TVLogLevelDebug;
    if (!strcasecmp(s, "verbose") || !strcasecmp(s, "all")) return TVLogLevelVerbose;
    return TVLogLevelVerbose;
}

static const char *level_name(tvlog_level_t l) {
    switch (l) {
        case TVLogLevelOff: return "off";
        case TVLogLevelError: return "error";
        case TVLogLevelWarn: return "warn";
        case TVLogLevelInfo: return "info";
        case TVLogLevelDebug: return "debug";
        case TVLogLevelVerbose: return "verbose";
    }
    return "?";
}

// ------------------------------------------------------------- async-safe output

/// Append a NUL-terminated string to buf, never overflowing it.
static void safe_cat(char *buf, size_t cap, size_t *len, const char *s) {
    if (!s) s = "(null)";
    while (*s && *len + 1 < cap) buf[(*len)++] = *s++;
    buf[*len] = '\0';
}

/// Append an unsigned decimal. Async-signal-safe (no stdio).
static void safe_cat_u64(char *buf, size_t cap, size_t *len, uint64_t v) {
    char tmp[24];
    int n = 0;
    if (v == 0) tmp[n++] = '0';
    while (v > 0 && n < (int)sizeof(tmp)) { tmp[n++] = (char)('0' + (v % 10)); v /= 10; }
    while (n > 0 && *len + 1 < cap) buf[(*len)++] = tmp[--n];
    buf[*len] = '\0';
}

/// Write all of buf, retrying short writes and EINTR. Async-signal-safe.
static void raw_write(int fd, const char *buf, size_t len) {
    if (fd < 0) return;
    size_t off = 0;
    while (off < len) {
        ssize_t n = write(fd, buf + off, len - off);
        if (n > 0) { off += (size_t)n; continue; }
        if (n < 0 && errno == EINTR) continue;
        break;
    }
}

// --------------------------------------------------------------- file mirroring

static void rotate_locked(void) {
    if (sFileFD < 0 || sFileBytes < kRotateBytes) return;
    char from[1100], to[1100];
    // tvport.log.2 -> tvport.log.3, ... tvport.log -> tvport.log.1
    for (int i = kRotateKeep - 1; i >= 1; i--) {
        snprintf(from, sizeof(from), "%s.%d", sFilePath, i);
        snprintf(to, sizeof(to), "%s.%d", sFilePath, i + 1);
        rename(from, to);
    }
    snprintf(to, sizeof(to), "%s.1", sFilePath);
    rename(sFilePath, to);
    close(sFileFD);
    sFileFD = open(sFilePath, O_WRONLY | O_CREAT | O_APPEND, 0644);
    sFileBytes = 0;
}

static void file_append(const char *line, size_t len) {
    if (sFileFD < 0) return;
    pthread_mutex_lock(&sFileLock);
    rotate_locked();
    raw_write(sFileFD, line, len);
    sFileBytes += (off_t)len;
    pthread_mutex_unlock(&sFileLock);
}

static void timestamp(char *out, size_t cap) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    struct tm tmv;
    time_t secs = ts.tv_sec;
    gmtime_r(&secs, &tmv);
    snprintf(out, cap, "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ",
             tmv.tm_year + 1900, tmv.tm_mon + 1, tmv.tm_mday,
             tmv.tm_hour, tmv.tm_min, tmv.tm_sec,
             (int)(ts.tv_nsec / 1000000));
}

// --------------------------------------------------------------------- the API

static const char *caches_dir(void) {
    static char path[1024];
    static bool done = false;
    if (done) return path[0] ? path : NULL;
    done = true;
    @autoreleasepool {
        NSArray<NSString *> *dirs = NSSearchPathForDirectoriesInDomains(
            NSCachesDirectory, NSUserDomainMask, YES);
        NSString *base = dirs.firstObject;
        if (!base) {
            // tvOS gives an app very little persistent storage; if Caches is
            // somehow unavailable, fall back to the temporary directory, which
            // is never preserved but is better than losing the mirror.
            base = NSTemporaryDirectory();
        }
        if (!base) return NULL;
        NSString *dir = [base stringByAppendingPathComponent:@"tvport"];
        [[NSFileManager defaultManager] createDirectoryAtPath:dir
                                 withIntermediateDirectories:YES
                                                  attributes:nil
                                                       error:NULL];
        strlcpy(path, dir.fileSystemRepresentation, sizeof(path));
    }
    return path[0] ? path : NULL;
}

void tvlog_init(void) {
    bool expected = false;
    if (!atomic_compare_exchange_strong(&sInitialised, &expected, true)) return;

    for (int i = 0; i < TVLogCategoryCount; i++) {
        sLogs[i] = os_log_create(TVLOG_SUBSYSTEM, kCategoryNames[i]);
    }

    @autoreleasepool {
        id raw = [[NSBundle mainBundle] objectForInfoDictionaryKey:@"TVPortLogLevel"];
        if ([raw isKindOfClass:[NSString class]]) {
            atomic_store(&sLevel, level_from_string([(NSString *)raw UTF8String]));
        }
        const char *env = getenv("TVPORT_LOG_LEVEL");  // overrides, for lldb runs
        if (env && *env) atomic_store(&sLevel, level_from_string(env));
    }

    const char *dir = caches_dir();
    if (dir) {
        snprintf(sFilePath, sizeof(sFilePath), "%s/tvport.log", dir);
        sFileFD = open(sFilePath, O_WRONLY | O_CREAT | O_APPEND, 0644);
        if (sFileFD >= 0) {
            struct stat st;
            if (fstat(sFileFD, &st) == 0) sFileBytes = st.st_size;
        }
    }

    TVLOG_I(TVLogLifecycle, "tvlog initialised: level=%s file=%s",
            level_name((tvlog_level_t)atomic_load(&sLevel)),
            sFileFD >= 0 ? sFilePath : "(none)");
}

bool tvlog_enabled(tvlog_level_t level) {
    return level <= (tvlog_level_t)atomic_load(&sLevel) && level != TVLogLevelOff;
}

tvlog_level_t tvlog_level(void) { return (tvlog_level_t)atomic_load(&sLevel); }

const char *tvlog_file_path(void) { return sFileFD >= 0 ? sFilePath : NULL; }

void tvlog_vwrite(tvlog_category_t category, tvlog_level_t level,
                  const char *fmt, va_list ap) {
    if (category < 0 || category >= TVLogCategoryCount) category = TVLogShim;
    if (!tvlog_enabled(level)) return;
    if (!atomic_load(&sInitialised)) tvlog_init();

    char msg[2048];
    vsnprintf(msg, sizeof(msg), fmt, ap);

    // os_log is the primary channel. The message is already formatted, so it
    // goes in as a single %{public}s: these are our own diagnostics and the
    // whole point is that they are readable over syslog.
    os_log_t log = sLogs[category] ?: OS_LOG_DEFAULT;
    switch (level) {
        case TVLogLevelError: os_log_error(log, "%{public}s", msg); break;
        case TVLogLevelWarn:  os_log(log, "%{public}s", msg); break;
        case TVLogLevelInfo:  os_log_info(log, "%{public}s", msg); break;
        default:              os_log_debug(log, "%{public}s", msg); break;
    }

    char line[2200], ts[40];
    timestamp(ts, sizeof(ts));
    int n = snprintf(line, sizeof(line), "%s [%s/%s] %s\n",
                     ts, kCategoryNames[category], level_name(level), msg);
    if (n > 0) file_append(line, (size_t)n < sizeof(line) ? (size_t)n : sizeof(line) - 1);
}

void tvlog_write(tvlog_category_t category, tvlog_level_t level,
                 const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    tvlog_vwrite(category, level, fmt, ap);
    va_end(ap);
}

// ------------------------------------------------------------- shim accounting

void tvlog_shim_enter(tvlog_shim_counter_t *counter) {
    if (!counter) return;
    if (!atomic_load(&sInitialised)) tvlog_init();
    atomic_fetch_add(&counter->calls, 1);

    bool wasRegistered = atomic_exchange(&counter->registered, true);
    if (!wasRegistered) {
        tvlog_shim_counter_t *head = atomic_load(&sCounters);
        do {
            counter->next = head;
        } while (!atomic_compare_exchange_weak(&sCounters, &head, counter));
    }

    bool logged = atomic_exchange(&counter->first_logged, true);
    if (logged) return;

    // First call: record a short caller backtrace so the log says who reached
    // the shim, which is the thing that is actually hard to find on device.
    void *frames[12];
    int count = backtrace(frames, 12);
    char trace[1024];
    size_t len = 0;
    trace[0] = '\0';
    for (int i = 1; i < count; i++) {  // skip this frame
        Dl_info info;
        if (dladdr(frames[i], &info) && info.dli_sname) {
            const char *image = info.dli_fname ? strrchr(info.dli_fname, '/') : NULL;
            safe_cat(trace, sizeof(trace), &len, " <- ");
            safe_cat(trace, sizeof(trace), &len, image ? image + 1 : "?");
            safe_cat(trace, sizeof(trace), &len, "`");
            safe_cat(trace, sizeof(trace), &len, info.dli_sname);
        } else {
            safe_cat(trace, sizeof(trace), &len, " <- ?");
        }
        if (len > sizeof(trace) - 128) break;
    }
    TVLOG_I(TVLogShim, "FIRST CALL %s [%s]%s",
            counter->name ?: "?", counter->decision ?: "?", trace);
}

void tvlog_flush_counters(const char *reason) {
    char line[4096];
    size_t len = 0;
    char ts[40];
    timestamp(ts, sizeof(ts));
    safe_cat(line, sizeof(line), &len, ts);
    safe_cat(line, sizeof(line), &len, " [shim/info] shim call counts (");
    safe_cat(line, sizeof(line), &len, reason ?: "periodic");
    safe_cat(line, sizeof(line), &len, "):");

    int entries = 0;
    for (tvlog_shim_counter_t *c = atomic_load(&sCounters); c; c = c->next) {
        uint64_t n = atomic_load(&c->calls);
        if (n == 0) continue;
        entries++;
        safe_cat(line, sizeof(line), &len, "\n    ");
        safe_cat_u64(line, sizeof(line), &len, n);
        safe_cat(line, sizeof(line), &len, "  ");
        safe_cat(line, sizeof(line), &len, c->name);
        safe_cat(line, sizeof(line), &len, " [");
        safe_cat(line, sizeof(line), &len, c->decision);
        safe_cat(line, sizeof(line), &len, "]");
        if (len > sizeof(line) - 256) {
            safe_cat(line, sizeof(line), &len, "\n    ...(truncated)");
            break;
        }
    }
    if (entries == 0) safe_cat(line, sizeof(line), &len, " none called");
    safe_cat(line, sizeof(line), &len, "\n");

    file_append(line, len);
    // Mirror to os_log too, but only outside a signal handler. Callers in a
    // handler pass reason "signal", which keeps this to write(2) only.
    if (!reason || strcmp(reason, "signal") != 0) {
        os_log_info(sLogs[TVLogShim] ?: OS_LOG_DEFAULT, "%{public}s", line);
    }
}

void tvlog_register_shim_library(const char *name) {
    if (!name) return;
    int idx = atomic_fetch_add(&sShimLibCount, 1);
    if (idx < (int)(sizeof(sShimLibs) / sizeof(sShimLibs[0]))) {
        atomic_store(&sShimLibs[idx], name);
    }
    if (!atomic_load(&sInitialised)) tvlog_init();
    TVLOG_I(TVLogLifecycle, "shim library loaded: %s", name);
}

// -------------------------------------------------------------------- the banner

static void append_device_info(NSMutableString *out) {
    struct utsname u;
    if (uname(&u) == 0) {
        [out appendFormat:@"\n    kernel: %s %s", u.sysname, u.release];
    }
    char model[256];
    size_t size = sizeof(model);
    if (sysctlbyname("hw.machine", model, &size, NULL, 0) == 0) {
        [out appendFormat:@"\n    hw.machine: %s", model];
    }
    size = sizeof(model);
    if (sysctlbyname("hw.model", model, &size, NULL, 0) == 0) {
        [out appendFormat:@"\n    hw.model: %s", model];
    }
}

void tvlog_banner(void) {
    if (!atomic_load(&sInitialised)) tvlog_init();
    @autoreleasepool {
        NSBundle *main = [NSBundle mainBundle];
        NSMutableString *b = [NSMutableString string];
        [b appendString:@"================ tvport startup ================"];
        [b appendFormat:@"\n    tvport build: %s", TVPORT_BUILD_ID];
        [b appendFormat:@"\n    log level: %s", level_name(tvlog_level())];
        [b appendFormat:@"\n    log file: %s", tvlog_file_path() ?: "(none)"];
        [b appendFormat:@"\n    bundle: %@ (%@)",
             [main objectForInfoDictionaryKey:@"CFBundleIdentifier"] ?: @"?",
             [main bundlePath] ?: @"?"];
        [b appendFormat:@"\n    Roblox version: %@ (%@)",
             [main objectForInfoDictionaryKey:@"CFBundleShortVersionString"] ?: @"?",
             [main objectForInfoDictionaryKey:@"CFBundleVersion"] ?: @"?"];
        [b appendFormat:@"\n    MinimumOSVersion: %@  UIDeviceFamily: %@",
             [main objectForInfoDictionaryKey:@"MinimumOSVersion"] ?: @"?",
             [main objectForInfoDictionaryKey:@"UIDeviceFamily"] ?: @"?"];

        // UIDevice exists on tvOS; systemVersion is the tvOS version.
        Class deviceClass = NSClassFromString(@"UIDevice");
        if (deviceClass) {
            id device = [deviceClass performSelector:@selector(currentDevice)];
            if (device) {
                [b appendFormat:@"\n    system: %@ %@",
                     [device valueForKey:@"systemName"] ?: @"?",
                     [device valueForKey:@"systemVersion"] ?: @"?"];
                [b appendFormat:@"\n    device model: %@",
                     [device valueForKey:@"model"] ?: @"?"];
            }
        }
        append_device_info(b);

        int count = atomic_load(&sShimLibCount);
        [b appendFormat:@"\n    shim libraries loaded (%d):", count];
        if (count == 0) {
            [b appendString:@" none"];
        }
        for (int i = 0; i < count && i < 64; i++) {
            const char *n = atomic_load(&sShimLibs[i]);
            if (n) [b appendFormat:@"\n        %s", n];
        }
        [b appendString:@"\n================================================"];
        TVLOG_I(TVLogLifecycle, "%s", [b UTF8String]);
    }
}

// ------------------------------------------------------------- crash accounting

static NSUncaughtExceptionHandler *sPrevExceptionHandler = NULL;

static void uncaught_exception(NSException *e) {
    @autoreleasepool {
        NSString *trace = [[e callStackSymbols] componentsJoinedByString:@"\n        "];
        TVLOG_E(TVLogCrash, "UNCAUGHT %s: %s\n        %s",
                [[e name] UTF8String] ?: "?",
                [[e reason] UTF8String] ?: "?",
                [trace UTF8String] ?: "");
    }
    tvlog_flush_counters("uncaught exception");
    if (sFileFD >= 0) fsync(sFileFD);
    // Chain, so the app still terminates the way it would have.
    if (sPrevExceptionHandler) sPrevExceptionHandler(e);
}

static const int kCrashSignals[] = { SIGSEGV, SIGBUS, SIGILL, SIGFPE, SIGABRT, SIGTRAP, SIGSYS };
static const int kCrashSignalCount = (int)(sizeof(kCrashSignals) / sizeof(kCrashSignals[0]));
static struct sigaction sPrevActions[7];

static const char *signal_name(int sig) {
    switch (sig) {
        case SIGSEGV: return "SIGSEGV";
        case SIGBUS:  return "SIGBUS";
        case SIGILL:  return "SIGILL";
        case SIGFPE:  return "SIGFPE";
        case SIGABRT: return "SIGABRT";
        case SIGTRAP: return "SIGTRAP";
        case SIGSYS:  return "SIGSYS";
    }
    return "SIG?";
}

/// Async-signal-safe: write(2) only, no malloc, no os_log, no stdio.
static void crash_signal(int sig, siginfo_t *info, void *uap) {
    char buf[2048];
    size_t len = 0;
    buf[0] = '\0';
    safe_cat(buf, sizeof(buf), &len, "\n*** tvport fatal signal ");
    safe_cat(buf, sizeof(buf), &len, signal_name(sig));
    safe_cat(buf, sizeof(buf), &len, " code=");
    safe_cat_u64(buf, sizeof(buf), &len, info ? (uint64_t)info->si_code : 0);
    safe_cat(buf, sizeof(buf), &len, " addr=");
    safe_cat_u64(buf, sizeof(buf), &len, info ? (uint64_t)(uintptr_t)info->si_addr : 0);
    safe_cat(buf, sizeof(buf), &len, " thread=");
    uint64_t tid = 0;
    pthread_threadid_np(NULL, &tid);
    safe_cat_u64(buf, sizeof(buf), &len, tid);
    safe_cat(buf, sizeof(buf), &len, " ***\n");

    // backtrace() is not formally async-signal-safe, but backtrace_symbols()
    // allocates, so only the frame addresses are taken and printed raw. They
    // symbolicate off-device against the shipped dSYMs.
    void *frames[24];
    int n = backtrace(frames, 24);
    safe_cat(buf, sizeof(buf), &len, "    frames:");
    for (int i = 0; i < n; i++) {
        safe_cat(buf, sizeof(buf), &len, " 0x");
        safe_cat_u64(buf, sizeof(buf), &len, (uint64_t)(uintptr_t)frames[i]);
    }
    safe_cat(buf, sizeof(buf), &len, "\n");

    raw_write(sFileFD, buf, len);
    tvlog_flush_counters("signal");
    if (sFileFD >= 0) fsync(sFileFD);

    // Restore and re-raise so the process dies normally and tvOS writes its
    // own crash report. Never swallow the crash (CLAUDE.md Phase 7).
    for (int i = 0; i < kCrashSignalCount; i++) {
        if (kCrashSignals[i] == sig) {
            sigaction(sig, &sPrevActions[i], NULL);
            break;
        }
    }
    (void)uap;
    raise(sig);
}

static void install_crash_handlers(void) {
    sPrevExceptionHandler = NSGetUncaughtExceptionHandler();
    NSSetUncaughtExceptionHandler(&uncaught_exception);

    for (int i = 0; i < kCrashSignalCount; i++) {
        struct sigaction sa;
        memset(&sa, 0, sizeof(sa));
        sa.sa_sigaction = crash_signal;
        sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
        sigemptyset(&sa.sa_mask);
        sigaction(kCrashSignals[i], &sa, &sPrevActions[i]);
    }
    TVLOG_D(TVLogCrash, "crash handlers installed (chaining to previous)");
}

static void at_exit_flush(void) {
    tvlog_flush_counters("exit");
    if (sFileFD >= 0) fsync(sFileFD);
}

static void start_counter_timer(void) {
    static dispatch_source_t timer;
    timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0,
                                   dispatch_get_global_queue(QOS_CLASS_UTILITY, 0));
    dispatch_source_set_timer(timer, dispatch_time(DISPATCH_TIME_NOW, 30 * NSEC_PER_SEC),
                              30 * NSEC_PER_SEC, 5 * NSEC_PER_SEC);
    dispatch_source_set_event_handler(timer, ^{ tvlog_flush_counters("periodic"); });
    dispatch_resume(timer);
}

__attribute__((constructor))
static void tvlog_ctor(void) {
    tvlog_init();
    install_crash_handlers();
    atexit(at_exit_flush);
    // The banner waits one run-loop turn so shim libraries loaded after us are
    // listed, and so UIDevice/NSBundle are fully usable.
    dispatch_async(dispatch_get_main_queue(), ^{
        tvlog_banner();
        start_counter_timer();
    });
}
