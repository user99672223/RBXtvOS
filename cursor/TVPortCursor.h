//
//  TVPortCursor.h — Siri Remote cursor for screens that only accept taps
//  (CLAUDE.md Phase 6)
//
//  Roblox's gameplay takes a game controller, which the client supports
//  natively. Its menus, sign-in and settings screens were built for touch and
//  have no focusable UIKit controls the tvOS focus engine can drive, so
//  without a pointer they are unreachable. This draws one and turns a click
//  into a tap.
//
//  Nothing here modifies Roblox's code. The overlay lives in its own window
//  above the app's, and input is read through GameController.
//
#ifndef TVPORT_CURSOR_H
#define TVPORT_CURSOR_H

#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>

NS_ASSUME_NONNULL_BEGIN

/// How a click at the cursor position was delivered. Logged per click so the
/// report can say which screens each strategy actually covers.
typedef NS_ENUM(NSInteger, TVPortClickRoute) {
    TVPortClickRouteNone = 0,
    TVPortClickRouteControl,       // preference 1: UIControl action dispatch
    TVPortClickRouteViewTouches,   // preference 2: touchesBegan/Ended on the view
    TVPortClickRouteSynthesized,   // preference 3: synthesized event (private API)
    TVPortClickRouteGesture,       // a gesture recognizer on the hit view
};

@interface TVPortCursor : NSObject

@property (class, readonly) TVPortCursor *shared;

/// Installs the overlay and starts listening for controllers. Safe to call
/// more than once; only the first call does anything.
- (void)install;

/// Visible state. Play/Pause toggles it; it is hidden automatically while an
/// extended gamepad (a real game controller) is the current controller.
@property (nonatomic, readonly, getter=isVisible) BOOL visible;
- (void)setVisible:(BOOL)visible reason:(const char *)reason;
- (void)toggleVisibleWithReason:(const char *)reason;

/// Cursor position in screen coordinates.
@property (nonatomic, readonly) CGPoint position;

/// Points per second at full d-pad deflection.
@property (nonatomic) CGFloat speed;

/// Deliver a click at the current position. Returns how it was routed.
- (TVPortClickRoute)clickAtCurrentPosition;

@end

NS_ASSUME_NONNULL_END

#endif /* TVPORT_CURSOR_H */
