//
//  TVPortTouchSynth.h — synthesizing a UITouch for the engine's render view
//  (CLAUDE.md Phase 6, click-to-tap preference 2)
//
//  A UIControl can be driven with sendActionsForControlEvents:, which is
//  public and needs nothing from us. Roblox's gameplay and most of its UI are
//  not UIControls: they are drawn into one engine-owned view that implements
//  touchesBegan:withEvent: and friends. Calling those methods needs a UITouch
//  whose location, window, view and phase are set, and UIKit exposes no public
//  way to make one: the setters are private ivars.
//
//  So this is private API, and it is treated as such:
//
//    * Every ivar is looked up at runtime with class_getInstanceVariable and
//      the whole path is refused if any of them is missing, so an OS change
//      degrades to "cursor cannot click this view" instead of crashing.
//    * The ivar names used here must be backed by evidence source 1 (the
//      device's own UIKitCore), recorded by the `uitouch-ivars` and
//      `uitouch-class` queries in evidence/queries/device_queries.json and
//      written up in evidence/shims.md.
//    * availability() reports what was found so the report can state exactly
//      which parts were verified on tvOS 27.2 and which were not.
//
#ifndef TVPORT_TOUCH_SYNTH_H
#define TVPORT_TOUCH_SYNTH_H

#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>

NS_ASSUME_NONNULL_BEGIN

typedef NS_ENUM(NSInteger, TVPortSynthSupport) {
    TVPortSynthUnknown = 0,
    TVPortSynthUnavailable,   // required ivars not present: do not use
    TVPortSynthAvailable,     // all required ivars found
};

@interface TVPortTouchSynth : NSObject

/// Probes UITouch's layout once and caches the result. Logs what it found.
+ (TVPortSynthSupport)support;

/// Human-readable summary of the probe, for the startup banner and REPORT.md.
+ (NSString *)supportDescription;

/// Deliver a tap to `view` at `point` (in `view`'s coordinates) by calling its
/// touch methods with a synthesized touch. Returns NO if unsupported or if the
/// view does not implement the touch methods.
+ (BOOL)tapView:(UIView *)view atPoint:(CGPoint)point inWindow:(UIWindow *)window;

@end

NS_ASSUME_NONNULL_END

#endif /* TVPORT_TOUCH_SYNTH_H */
