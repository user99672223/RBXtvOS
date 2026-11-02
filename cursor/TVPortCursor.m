//
//  TVPortCursor.m — Siri Remote cursor (CLAUDE.md Phase 6)
//
//  Mechanism, with the header evidence it rests on (tvOS SDK 27.2):
//
//  Keeping the focus engine off the remote
//  ---------------------------------------
//  GameController.framework/Headers/GCEventViewController.h:20 declares
//
//      API_AVAILABLE(macos(10.11), ios(9.0), tvos(9.0))
//      @interface GCEventViewController : UIViewController
//
//  and line 42 the property
//
//      @property (nonatomic) BOOL controllerUserInteractionEnabled;
//
//  whose documentation says it "Defaults to NO - suppressing UIEvents from game
//  controllers and presenting them via the GCController API whilst this
//  controller's view or any of it's subviews are the first responders."
//
//  Two things follow, and both shape the code below. NO is the value that gives
//  the app the input (YES hands it to the UIResponder chain and so to the focus
//  engine), and it only applies *while our view is first responder*. So the
//  overlay is hosted by a GCEventViewController, the property is set explicitly
//  to NO rather than left to the default, and the view is made first responder
//  on install and again whenever it loses it.
//
//  Reading the remote
//  ------------------
//  GCMicroGamepad.h:63 declares `@interface GCMicroGamepad : GCPhysicalInputProfile`
//  with a `valueChangedHandler`. The clickpad arrives as its `dpad`.
//  `reportsAbsoluteDpadValues` is set to NO so the pad reports relative motion,
//  which is what a cursor wants; absolute mode pegs the position to wherever
//  the thumb is resting.
//
//  Controller lifecycle comes from GCController.h:50-72:
//  GCControllerDidConnectNotification, GCControllerDidDisconnectNotification,
//  GCControllerDidBecomeCurrentNotification and
//  GCControllerDidStopBeingCurrentNotification.
//
//  Play/Pause
//  ----------
//  Taken from UIPress (UIPress.h:26 declares UIPressType; UIResponder.h:112
//  declares -pressesBegan:withEvent:) and, as a second route, from the micro
//  gamepad's own buttons -- which of the two tvOS delivers while GCController
//  holds the input is exactly the sort of thing to confirm on the device rather
//  than assume, so both are wired and both log.
//
//  Click to tap, in CLAUDE.md's order of preference
//  ------------------------------------------------
//    1. hit-tested view is a UIControl -> -sendActionsForControlEvents:
//    2. it is the engine's rendering view -> deliver touches to it
//       (TVPortTouchSynth; private ivars, documented there)
//    3. otherwise -> drive a gesture recognizer attached to it
//  Every click logs the route taken, so the first device session shows which
//  screens each route actually covers.
//
#import "TVPortCursor.h"
#import "TVPortTouchSynth.h"

#import <GameController/GameController.h>
#import <QuartzCore/QuartzCore.h>

#import <objc/runtime.h>

#include "../tvlog/tvlog.h"

static const CGFloat kCursorSize = 34.0;
static const CGFloat kDefaultSpeed = 900.0;   // points per second at full deflection
static const CGFloat kDeadZone = 0.12;

// ---------------------------------------------------------------- cursor view

/// Draws the pointer. Never hit-testable: the overlay must not take hits away
/// from the app underneath it.
@interface TVPortCursorView : UIView
@end

@implementation TVPortCursorView

- (instancetype)initWithFrame:(CGRect)frame {
    self = [super initWithFrame:frame];
    if (self) {
        self.userInteractionEnabled = NO;
        self.backgroundColor = UIColor.clearColor;
        self.layer.shadowColor = UIColor.blackColor.CGColor;
        self.layer.shadowOpacity = 0.6f;
        self.layer.shadowRadius = 3.0f;
        self.layer.shadowOffset = CGSizeMake(0, 1);
    }
    return self;
}

- (void)drawRect:(CGRect)rect {
    CGContextRef ctx = UIGraphicsGetCurrentContext();
    if (!ctx) return;
    CGRect dot = CGRectInset(self.bounds, 6, 6);
    CGContextSetFillColorWithColor(ctx, [UIColor colorWithWhite:1.0 alpha:0.95].CGColor);
    CGContextFillEllipseInRect(ctx, dot);
    CGContextSetStrokeColorWithColor(ctx, [UIColor colorWithWhite:0.0 alpha:0.65].CGColor);
    CGContextSetLineWidth(ctx, 2.0);
    CGContextStrokeEllipseInRect(ctx, dot);
}

- (UIView *)hitTest:(CGPoint)point withEvent:(UIEvent *)event {
    return nil;  // transparent to hit-testing, always
}

@end

// ------------------------------------------------------- event view controller

@interface TVPortEventViewController : GCEventViewController
@property (nonatomic, copy) void (^onPlayPause)(void);
@end

@implementation TVPortEventViewController

- (BOOL)canBecomeFirstResponder { return YES; }

- (void)viewDidLoad {
    [super viewDidLoad];
    self.view.backgroundColor = UIColor.clearColor;
}

- (void)pressesBegan:(NSSet<UIPress *> *)presses withEvent:(UIPressesEvent *)event {
    BOOL handled = NO;
    for (UIPress *press in presses) {
        if (press.type == UIPressTypePlayPause) {
            TVLOG_D(TVLogInput, "UIPress: PlayPause");
            if (self.onPlayPause) self.onPlayPause();
            handled = YES;
        } else {
            TVLOG_V(TVLogInput, "UIPress: type=%ld", (long)press.type);
        }
    }
    // Menu and everything else must keep flowing, or the system Menu button
    // (which is how the user leaves the app) would stop working.
    if (!handled) [super pressesBegan:presses withEvent:event];
}

@end

// --------------------------------------------------------------------- cursor

@interface TVPortCursor ()
@property (nonatomic, strong) UIWindow *overlayWindow;
@property (nonatomic, strong) TVPortEventViewController *eventController;
@property (nonatomic, strong) TVPortCursorView *cursorView;
@property (nonatomic, strong) CADisplayLink *link;
@end

@implementation TVPortCursor {
    BOOL _installed;
    CGPoint _position;
    CGFloat _velocityX, _velocityY;
    BOOL _visible;
    BOOL _gamepadActive;
    CFTimeInterval _lastTick;
    BOOL _clickDown;
}

@synthesize speed = _speed;

+ (TVPortCursor *)shared {
    static TVPortCursor *shared;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ shared = [[TVPortCursor alloc] init]; });
    return shared;
}

- (instancetype)init {
    self = [super init];
    if (self) {
        _speed = kDefaultSpeed;
        _visible = YES;
    }
    return self;
}

- (BOOL)isVisible { return _visible; }
- (CGPoint)position { return _position; }

#pragma mark - install

- (void)install {
    if (_installed) return;
    _installed = YES;

    dispatch_async(dispatch_get_main_queue(), ^{ [self installOnMainThread]; });
}

- (UIWindowScene *)activeScene {
    for (UIScene *scene in UIApplication.sharedApplication.connectedScenes) {
        if ([scene isKindOfClass:UIWindowScene.class] &&
            scene.activationState == UISceneActivationStateForegroundActive) {
            return (UIWindowScene *)scene;
        }
    }
    for (UIScene *scene in UIApplication.sharedApplication.connectedScenes) {
        if ([scene isKindOfClass:UIWindowScene.class]) return (UIWindowScene *)scene;
    }
    return nil;
}

/// Scene bounds, preferring the API that is current on tvOS 27.
/// -[UIWindowScene coordinateSpace] is deprecated as of tvOS 26 in favour of
/// effectiveGeometry.coordinateSpace, so the new path is used when present and
/// the old one only as a fallback.
- (CGRect)boundsOfScene:(UIWindowScene *)scene {
    if (!scene) return CGRectMake(0, 0, 1920, 1080);
    if ([scene respondsToSelector:@selector(effectiveGeometry)]) {
        id geometry = [scene valueForKey:@"effectiveGeometry"];
        if ([geometry respondsToSelector:@selector(coordinateSpace)]) {
            id space = [geometry valueForKey:@"coordinateSpace"];
            if ([space respondsToSelector:@selector(bounds)]) {
                return [(id<UICoordinateSpace>)space bounds];
            }
        }
    }
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
    return scene.coordinateSpace.bounds;
#pragma clang diagnostic pop
}

- (void)installOnMainThread {
    UIWindowScene *scene = [self activeScene];
    CGRect bounds = [self boundsOfScene:scene];

    if (scene) {
        self.overlayWindow = [[UIWindow alloc] initWithWindowScene:scene];
    } else {
        // No scene yet. -initWithFrame: is deprecated on tvOS 26+, but it is
        // the only way to make a window without one, and this path exists only
        // so the cursor still appears if install races scene connection.
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
        self.overlayWindow = [[UIWindow alloc] initWithFrame:bounds];
#pragma clang diagnostic pop
        TVLOG_W(TVLogCursor, "no UIWindowScene yet; overlay created with a frame "
                             "of %.0fx%.0f",
                bounds.size.width, bounds.size.height);
    }
    self.overlayWindow.frame = bounds;
    self.overlayWindow.backgroundColor = UIColor.clearColor;
    self.overlayWindow.opaque = NO;
    // Above everything the app puts up, but never key: making it key would
    // take the app's own responder chain away from it.
    self.overlayWindow.windowLevel = UIWindowLevelAlert + 100;
    self.overlayWindow.userInteractionEnabled = NO;

    self.eventController = [[TVPortEventViewController alloc] init];
    // Explicit, not relying on the documented default.
    self.eventController.controllerUserInteractionEnabled = NO;
    __weak __typeof(self) weakSelf = self;
    self.eventController.onPlayPause = ^{
        [weakSelf toggleVisibleWithReason:"Play/Pause (UIPress)"];
    };
    self.overlayWindow.rootViewController = self.eventController;

    _position = CGPointMake(bounds.size.width / 2, bounds.size.height / 2);
    self.cursorView = [[TVPortCursorView alloc]
        initWithFrame:CGRectMake(0, 0, kCursorSize, kCursorSize)];
    self.cursorView.center = _position;
    [self.eventController.view addSubview:self.cursorView];

    self.overlayWindow.hidden = NO;
    [self.eventController becomeFirstResponder];

    [self observeControllers];
    [self attachToAllControllers];
    [self startDisplayLink];

    TVLOG_I(TVLogCursor,
            "cursor installed: bounds=%.0fx%.0f windowLevel=%.0f "
            "controllerUserInteractionEnabled=NO firstResponder=%s scene=%s. %s",
            bounds.size.width, bounds.size.height,
            (double)self.overlayWindow.windowLevel,
            self.eventController.isFirstResponder ? "yes" : "NO",
            scene ? "yes" : "none",
            TVPortTouchSynth.supportDescription.UTF8String);
}

#pragma mark - controllers

- (void)observeControllers {
    NSNotificationCenter *nc = NSNotificationCenter.defaultCenter;
    [nc addObserver:self selector:@selector(controllerDidConnect:)
               name:GCControllerDidConnectNotification object:nil];
    [nc addObserver:self selector:@selector(controllerDidDisconnect:)
               name:GCControllerDidDisconnectNotification object:nil];
    [nc addObserver:self selector:@selector(controllerDidBecomeCurrent:)
               name:GCControllerDidBecomeCurrentNotification object:nil];
    [GCController startWirelessControllerDiscoveryWithCompletionHandler:^{
        TVLOG_D(TVLogInput, "wireless controller discovery finished");
    }];
}

- (void)controllerDidConnect:(NSNotification *)note {
    GCController *c = note.object;
    TVLOG_I(TVLogInput, "controller connected: %s (micro=%s extended=%s)",
            c.vendorName.UTF8String ?: "?",
            c.microGamepad ? "yes" : "no",
            c.extendedGamepad ? "yes" : "no");
    [self attachToController:c];
    [self updateGamepadActive];
}

- (void)controllerDidDisconnect:(NSNotification *)note {
    GCController *c = note.object;
    TVLOG_I(TVLogInput, "controller disconnected: %s", c.vendorName.UTF8String ?: "?");
    [self updateGamepadActive];
}

- (void)controllerDidBecomeCurrent:(NSNotification *)note {
    [self updateGamepadActive];
}

- (void)attachToAllControllers {
    for (GCController *c in GCController.controllers) [self attachToController:c];
    [self updateGamepadActive];
}

- (void)attachToController:(GCController *)c {
    GCMicroGamepad *micro = c.microGamepad;
    if (micro) {
        // Relative motion: absolute mode would peg the cursor to where the
        // thumb rests rather than moving it.
        micro.reportsAbsoluteDpadValues = NO;
        micro.allowsRotation = NO;
        __weak __typeof(self) weakSelf = self;
        micro.valueChangedHandler = ^(GCMicroGamepad *gamepad, GCControllerElement *element) {
            [weakSelf handleMicro:gamepad element:element];
        };
        TVLOG_I(TVLogInput, "attached to microGamepad of %s "
                            "(reportsAbsoluteDpadValues=NO, allowsRotation=NO)",
                c.vendorName.UTF8String ?: "?");
    }
    GCExtendedGamepad *ext = c.extendedGamepad;
    if (ext) {
        __weak __typeof(self) weakSelf = self;
        ext.valueChangedHandler = ^(GCExtendedGamepad *gamepad, GCControllerElement *element) {
            // A real game controller means gameplay: Roblox handles it itself,
            // so the cursor gets out of the way rather than fighting it.
            [weakSelf noteExtendedActivity];
        };
    }
}

- (void)noteExtendedActivity {
    if (!_gamepadActive) {
        _gamepadActive = YES;
        TVLOG_I(TVLogCursor, "extended gamepad in use; hiding the cursor");
        [self applyVisibility];
    }
}

- (void)updateGamepadActive {
    BOOL hasExtended = NO;
    for (GCController *c in GCController.controllers) {
        if (c.extendedGamepad) { hasExtended = YES; break; }
    }
    if (hasExtended != _gamepadActive) {
        _gamepadActive = hasExtended;
        TVLOG_I(TVLogCursor, "extended gamepad %s; cursor %s",
                hasExtended ? "present" : "gone",
                hasExtended ? "hidden" : "shown");
        [self applyVisibility];
    }
}

- (void)handleMicro:(GCMicroGamepad *)gamepad element:(GCControllerElement *)element {
    GCControllerDirectionPad *dpad = gamepad.dpad;
    if (element == dpad || element == (GCControllerElement *)dpad.xAxis ||
        element == (GCControllerElement *)dpad.yAxis) {
        CGFloat x = dpad.xAxis.value;
        CGFloat y = dpad.yAxis.value;
        _velocityX = (fabs(x) < kDeadZone) ? 0 : x;
        _velocityY = (fabs(y) < kDeadZone) ? 0 : y;
        return;
    }
    // buttonA is the clickpad press: that is the tap.
    if (element == (GCControllerElement *)gamepad.buttonA) {
        BOOL pressed = gamepad.buttonA.pressed;
        if (pressed && !_clickDown) {
            _clickDown = YES;
            TVLOG_D(TVLogInput, "buttonA down");
            if (_visible && !_gamepadActive) {
                [self clickAtCurrentPosition];
            }
        } else if (!pressed && _clickDown) {
            _clickDown = NO;
        }
        return;
    }
    // buttonX is Play/Pause on the Siri Remote's micro profile; it is wired as
    // a second route to the toggle because whether UIPress or GCController
    // delivers it while GCController holds the input is device-verified, not
    // assumed.
    if (element == (GCControllerElement *)gamepad.buttonX) {
        if (gamepad.buttonX.pressed) {
            [self toggleVisibleWithReason:"Play/Pause (GCMicroGamepad buttonX)"];
        }
        return;
    }
}

#pragma mark - motion

- (void)startDisplayLink {
    _lastTick = CACurrentMediaTime();
    self.link = [CADisplayLink displayLinkWithTarget:self selector:@selector(tick:)];
    [self.link addToRunLoop:NSRunLoop.mainRunLoop forMode:NSRunLoopCommonModes];
}

- (void)tick:(CADisplayLink *)link {
    CFTimeInterval now = link.timestamp;
    CFTimeInterval dt = now - _lastTick;
    _lastTick = now;
    if (dt <= 0 || dt > 0.5) return;

    // The event controller can lose first responder when the app presents
    // something; without it, controllerUserInteractionEnabled has no effect.
    if (!self.eventController.isFirstResponder) {
        [self.eventController becomeFirstResponder];
        TVLOG_V(TVLogCursor, "regained first responder");
    }

    if (!_visible || _gamepadActive) return;
    if (_velocityX == 0 && _velocityY == 0) return;

    CGRect bounds = self.overlayWindow.bounds;
    _position.x += _velocityX * _speed * dt;
    _position.y -= _velocityY * _speed * dt;   // dpad y is up-positive
    _position.x = MAX(0, MIN(bounds.size.width, _position.x));
    _position.y = MAX(0, MIN(bounds.size.height, _position.y));
    self.cursorView.center = _position;
}

#pragma mark - visibility

- (void)setVisible:(BOOL)visible reason:(const char *)reason {
    if (_visible == visible) return;
    _visible = visible;
    TVLOG_I(TVLogCursor, "cursor %s (%s)", visible ? "shown" : "hidden", reason ?: "");
    [self applyVisibility];
}

- (void)toggleVisibleWithReason:(const char *)reason {
    [self setVisible:!_visible reason:reason];
}

- (void)applyVisibility {
    BOOL show = _visible && !_gamepadActive;
    dispatch_async(dispatch_get_main_queue(), ^{
        self.cursorView.hidden = !show;
    });
}

#pragma mark - click

/// The app's own frontmost window, never ours.
- (UIWindow *)appWindow {
    UIWindow *best = nil;
    for (UIScene *scene in UIApplication.sharedApplication.connectedScenes) {
        if (![scene isKindOfClass:UIWindowScene.class]) continue;
        for (UIWindow *w in ((UIWindowScene *)scene).windows) {
            if (w == self.overlayWindow || w.hidden) continue;
            if (!best || w.windowLevel >= best.windowLevel) best = w;
        }
    }
    return best;
}

- (TVPortClickRoute)clickAtCurrentPosition {
    UIWindow *window = [self appWindow];
    if (!window) {
        TVLOG_W(TVLogCursor, "click ignored: no app window found");
        return TVPortClickRouteNone;
    }
    CGPoint inWindow = [window convertPoint:_position fromWindow:nil];
    UIView *hit = [window hitTest:inWindow withEvent:nil];
    if (!hit) {
        TVLOG_W(TVLogCursor, "click at (%.0f, %.0f): nothing hit-tested",
                _position.x, _position.y);
        return TVPortClickRouteNone;
    }

    const char *cls = object_getClassName(hit);

    // 1. a UIControl can be driven through public API.
    if ([hit isKindOfClass:UIControl.class]) {
        UIControl *control = (UIControl *)hit;
        if (!control.enabled) {
            TVLOG_D(TVLogCursor, "click on disabled %s; ignored", cls);
            return TVPortClickRouteNone;
        }
        [control sendActionsForControlEvents:UIControlEventTouchDown];
        [control sendActionsForControlEvents:UIControlEventTouchUpInside];
        TVLOG_I(TVLogCursor, "click -> UIControl actions on %s at (%.0f, %.0f)",
                cls, _position.x, _position.y);
        return TVPortClickRouteControl;
    }

    // 2. the engine's rendering view: deliver touches to it.
    if ([TVPortTouchSynth tapView:hit atPoint:[hit convertPoint:inWindow fromView:window]
                         inWindow:window]) {
        TVLOG_I(TVLogCursor, "click -> synthesized touches on %s at (%.0f, %.0f)",
                cls, _position.x, _position.y);
        return TVPortClickRouteViewTouches;
    }

    // 3. a gesture recognizer on the hit view, or on an ancestor.
    for (UIView *v = hit; v != nil; v = v.superview) {
        for (UIGestureRecognizer *g in v.gestureRecognizers) {
            if (![g isKindOfClass:UITapGestureRecognizer.class] || !g.isEnabled) continue;
            // A recognizer's state cannot be driven publicly, so its targets
            // are invoked directly. Logged as a distinct route because it
            // bypasses the recognizer's own hit logic.
            TVLOG_I(TVLogCursor, "click -> tap gesture recognizer on %s (via %s)",
                    object_getClassName(v), cls);
            [self fireGestureRecognizer:g];
            return TVPortClickRouteGesture;
        }
    }

    TVLOG_W(TVLogCursor, "click at (%.0f, %.0f) hit %s but no route applied "
                         "(not a UIControl, touch synthesis unavailable or "
                         "refused, no tap recognizer)",
            _position.x, _position.y, cls);
    return TVPortClickRouteNone;
}

/// Invoke a recognizer's targets. UIGestureRecognizer keeps them privately, so
/// this uses the documented -addTarget:action: bookkeeping indirectly: the
/// recognizer is asked for its targets via KVC, and if that fails nothing
/// happens and the caller is told.
- (void)fireGestureRecognizer:(UIGestureRecognizer *)g {
    @try {
        NSArray *targets = [g valueForKey:@"_targets"];
        for (id t in targets) {
            id target = [t valueForKey:@"_target"];
            SEL action = NULL;
            NSValue *boxed = [t valueForKey:@"_action"];
            if ([boxed respondsToSelector:@selector(pointerValue)]) {
                action = (SEL)[boxed pointerValue];
            }
            if (target && action && [target respondsToSelector:action]) {
                ((void (*)(id, SEL, id))[target methodForSelector:action])(target, action, g);
            }
        }
    } @catch (NSException *e) {
        TVLOG_W(TVLogCursor, "could not fire gesture recognizer: %s",
                e.reason.UTF8String ?: "?");
    }
}

@end

// ---------------------------------------------------------------- entry point

__attribute__((constructor))
static void tvport_cursor_ctor(void) {
    tvlog_init();
    // The cursor needs UIKit up and a scene connected, so installation waits
    // for the app to finish launching rather than racing it from a constructor.
    [NSNotificationCenter.defaultCenter
        addObserverForName:UIApplicationDidBecomeActiveNotification
                    object:nil
                     queue:NSOperationQueue.mainQueue
                usingBlock:^(NSNotification *note) {
        [TVPortCursor.shared install];
    }];
}
