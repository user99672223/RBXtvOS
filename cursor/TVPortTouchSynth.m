//
//  TVPortTouchSynth.m
//
//  See the header for why this uses private ivars and how it is guarded.
//
#import "TVPortTouchSynth.h"

#import <objc/runtime.h>

#include "../tvlog/tvlog.h"

// Candidate ivar names, in the order they are tried, each slot holding the
// alternatives seen across releases. Which one was found is logged.
//
// These are no longer guesses. The device's own UIKitCore for tvOS 27.2
// (24K122) declares (uitouch-ivars in evidence/queries/device_queries.json):
//
//   @interface UITouch : NSObject <_UIResponderForwardableEventComponent> {
//       q _phase;  Q _tapCount;  d _timestamp;
//       @"UIWindow" _window;
//       @"UIResponder<_UIGestureRecognizerContainer>" _responder;
//       @"UIView" _cachedResponderView;
//       @"UIView" _warpedIntoView;
//       {CGPoint} _locationInWindow;
//       {CGPoint} _previousLocationInWindow;
//       ...
//   }
//
// The important correction: **there is no `_view` ivar.** An earlier version of
// this file required one, so the probe would have reported UNAVAILABLE and
// click-to-tap preference 2 would have been silently disabled for good -- the
// guard would have held, but the feature would simply never have worked. The
// view is carried by `_cachedResponderView`, with `_responder` holding the
// responder it was hit-tested to. Both are set: a UIView is a UIResponder, and
// -[UITouch view] derives from the responder on modern UIKit.
static const char *const kLocationIvars[]  = { "_locationInWindow", "_location", NULL };
static const char *const kPreviousIvars[]  = { "_previousLocationInWindow", "_previousLocation", NULL };
static const char *const kWindowIvars[]    = { "_window", NULL };
static const char *const kViewIvars[]      = { "_cachedResponderView", "_view", "_warpedIntoView", NULL };
static const char *const kResponderIvars[] = { "_responder", NULL };
static const char *const kPhaseIvars[]     = { "_phase", NULL };
static const char *const kTapCountIvars[]  = { "_tapCount", NULL };
static const char *const kTimestampIvars[] = { "_timestamp", NULL };

typedef struct {
    Ivar location;
    Ivar previousLocation;
    Ivar window;
    Ivar view;
    Ivar responder;
    Ivar phase;
    Ivar tapCount;
    Ivar timestamp;
} tvport_touch_layout_t;

static tvport_touch_layout_t sLayout;
static TVPortSynthSupport sSupport = TVPortSynthUnknown;
static NSMutableString *sReport;

static Ivar find_ivar(Class cls, const char *const *names, const char **foundName) {
    for (int i = 0; names[i]; i++) {
        Ivar iv = class_getInstanceVariable(cls, names[i]);
        if (iv) {
            if (foundName) *foundName = names[i];
            return iv;
        }
    }
    if (foundName) *foundName = NULL;
    return NULL;
}

static void note(NSMutableString *out, const char *slot, Ivar iv, const char *name) {
    if (iv) {
        [out appendFormat:@"\n        %-24s %s (offset %td, type %s)", slot, name,
             ivar_getOffset(iv), ivar_getTypeEncoding(iv) ?: "?"];
    } else {
        [out appendFormat:@"\n        %-24s NOT FOUND", slot];
    }
}

@implementation TVPortTouchSynth

+ (TVPortSynthSupport)support {
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        sReport = [NSMutableString stringWithString:@"UITouch synthesis probe:"];
        Class touchClass = NSClassFromString(@"UITouch");
        if (!touchClass) {
            [sReport appendString:@" UITouch class not found"];
            sSupport = TVPortSynthUnavailable;
            TVLOG_W(TVLogCursor, "%s", sReport.UTF8String);
            return;
        }
        const char *n = NULL;
        sLayout.location = find_ivar(touchClass, kLocationIvars, &n);
        note(sReport, "location", sLayout.location, n);
        sLayout.previousLocation = find_ivar(touchClass, kPreviousIvars, &n);
        note(sReport, "previousLocation", sLayout.previousLocation, n);
        sLayout.window = find_ivar(touchClass, kWindowIvars, &n);
        note(sReport, "window", sLayout.window, n);
        sLayout.view = find_ivar(touchClass, kViewIvars, &n);
        note(sReport, "view", sLayout.view, n);
        sLayout.responder = find_ivar(touchClass, kResponderIvars, &n);
        note(sReport, "responder", sLayout.responder, n);
        sLayout.phase = find_ivar(touchClass, kPhaseIvars, &n);
        note(sReport, "phase", sLayout.phase, n);
        sLayout.tapCount = find_ivar(touchClass, kTapCountIvars, &n);
        note(sReport, "tapCount", sLayout.tapCount, n);
        sLayout.timestamp = find_ivar(touchClass, kTimestampIvars, &n);
        note(sReport, "timestamp", sLayout.timestamp, n);

        // location, window, view and phase are the minimum a view needs for
        // locationInView: and the phase checks to behave. The rest are nice to
        // have and are set when present.
        BOOL ok = sLayout.location && sLayout.window && sLayout.view && sLayout.phase;
        sSupport = ok ? TVPortSynthAvailable : TVPortSynthUnavailable;
        [sReport appendFormat:@"\n        => %s", ok ? "AVAILABLE" : "UNAVAILABLE"];
        TVLOG_I(TVLogCursor, "%s", sReport.UTF8String);
    });
    return sSupport;
}

+ (NSString *)supportDescription {
    [self support];
    return sReport ?: @"(not probed)";
}

/// Set an object ivar without ARC complicating the ownership. The touch is
/// short-lived and the referents (window, view) outlive the call, so an
/// unretained store is correct and avoids over-releasing UIKit's objects.
static void set_object_ivar(id obj, Ivar iv, id value) {
    if (!iv) return;
    // Unretained store: the touch is short-lived and the referents (window,
    // view) outlive the call, so taking no ownership is correct here and
    // avoids over-releasing UIKit's objects when the touch is freed.
    void **slot = (void **)((char *)(__bridge void *)obj + ivar_getOffset(iv));
    *slot = (__bridge void *)value;
}

static void set_cgpoint_ivar(id obj, Ivar iv, CGPoint p) {
    if (!iv) return;
    void *slot = (char *)(__bridge void *)obj + ivar_getOffset(iv);
    memcpy(slot, &p, sizeof(p));
}

static void set_int_ivar(id obj, Ivar iv, NSInteger v) {
    if (!iv) return;
    void *slot = (char *)(__bridge void *)obj + ivar_getOffset(iv);
    memcpy(slot, &v, sizeof(v));
}

static void set_double_ivar(id obj, Ivar iv, double v) {
    if (!iv) return;
    void *slot = (char *)(__bridge void *)obj + ivar_getOffset(iv);
    memcpy(slot, &v, sizeof(v));
}

+ (BOOL)tapView:(UIView *)view atPoint:(CGPoint)point inWindow:(UIWindow *)window {
    if ([self support] != TVPortSynthAvailable) return NO;
    if (!view || !window) return NO;
    if (![view respondsToSelector:@selector(touchesBegan:withEvent:)]) return NO;

    CGPoint inWindow = [view convertPoint:point toView:nil];

    UITouch *touch = [[NSClassFromString(@"UITouch") alloc] init];
    if (!touch) return NO;

    set_cgpoint_ivar(touch, sLayout.location, inWindow);
    set_cgpoint_ivar(touch, sLayout.previousLocation, inWindow);
    set_object_ivar(touch, sLayout.window, window);
    set_object_ivar(touch, sLayout.view, view);
    // A UIView is a UIResponder, and -[UITouch view] derives from the
    // responder on this UIKit, so both slots point at the hit view.
    set_object_ivar(touch, sLayout.responder, view);
    set_int_ivar(touch, sLayout.tapCount, 1);
    set_double_ivar(touch, sLayout.timestamp, [[NSProcessInfo processInfo] systemUptime]);

    NSSet *touches = [NSSet setWithObject:touch];

    // UIEvent cannot be synthesized usefully without more private surface, and
    // a well-behaved touchesBegan: implementation only needs the touch. nil is
    // passed for the event and logged, so if an engine view turns out to need
    // it the log says so rather than it failing silently.
    set_int_ivar(touch, sLayout.phase, UITouchPhaseBegan);
    [view touchesBegan:touches withEvent:(UIEvent *)nil];

    set_cgpoint_ivar(touch, sLayout.previousLocation, inWindow);
    set_int_ivar(touch, sLayout.phase, UITouchPhaseEnded);
    set_double_ivar(touch, sLayout.timestamp, [[NSProcessInfo processInfo] systemUptime]);
    [view touchesEnded:touches withEvent:(UIEvent *)nil];

    // Drop our unretained references so nothing dangles if the touch outlives
    // the views (it should not, but this costs nothing).
    set_object_ivar(touch, sLayout.window, nil);
    set_object_ivar(touch, sLayout.view, nil);
    set_object_ivar(touch, sLayout.responder, nil);

    TVLOG_D(TVLogCursor, "synthesized tap on %s at (%.1f, %.1f) window (%.1f, %.1f)",
            object_getClassName(view), point.x, point.y, inWindow.x, inWindow.y);
    return YES;
}

@end
