//
//  proxy_UIKit.m — UIKit proxy: UIHoverGestureRecognizer
//
//  This is the one shim RobloxLib cannot load without.
//
//  Evidence
//  --------
//  Missing on device (source 1): the device's UIKit closure for tvOS 27.2
//  (24K122) holds 51,191 exported names, including everything re-exported from
//  UIKitCore, PrintKitUI and ShareSheet, and does not contain
//  _OBJC_CLASS_$_UIHoverGestureRecognizer.
//
//  Missing from the public API (source 2): tvOS SDK 27.2's UIKit.tbd does not
//  export it either. The SDK *does* declare the class -- the build proved it,
//  by rejecting a redeclaration against
//  UIKit.framework/Headers/UIHoverGestureRecognizer.h:22 -- but the
//  declaration carries API_UNAVAILABLE(tvos) and nothing on the platform
//  implements it. So the header is there, the class is not. This file therefore
//  implements the SDK's own declaration rather than inventing a parallel one,
//  which also guarantees the method signatures match exactly.
//
//  Contract (source 3), iOS SDK 26.2, same header, same line:
//
//      UIKIT_EXTERN API_AVAILABLE(ios(13.0)) API_UNAVAILABLE(watchos, tvos)
//      @interface UIHoverGestureRecognizer : UIGestureRecognizer
//
//  Referenced by (source 4): RobloxLib imports
//  _OBJC_CLASS_$_UIHoverGestureRecognizer non-weakly and eagerly. There is no
//  _OBJC_METACLASS_$_ import, so RobloxLib does not subclass it. No binary in
//  the bundle references zOffset, altitudeAngle, rollAngle, azimuthAngleInView:
//  or azimuthUnitVectorInView: in __objc_selrefs, so Roblox never calls them;
//  they are implemented anyway, because the SDK declares them and leaving them
//  out would make the class incomplete, and each returns the "unsupported
//  device" value the iOS header documents.
//
//  Decision: STUB.
//
//  Reason: the iOS header states outright that "On iOS, this gesture
//  recognizer doesn't recognize anything and is effectively a no-op." A
//  recognizer that stays in UIGestureRecognizerStatePossible and never
//  transitions is therefore not an approximation of iOS behaviour, it is the
//  same behaviour, and there is no hovering input device on an Apple TV in any
//  case. Implementing the SDK's declaration keeps isKindOfClass:,
//  addGestureRecognizer: and the delegate protocol working.
//
//  Status: UNTESTED (no device run yet).
//
#import <UIKit/UIKit.h>

#include "shimsupport.h"

TVPORT_SHIM_LIBRARY("proxy_UIKit (UIHoverGestureRecognizer)")

// The class is declared in the tvOS SDK but marked unavailable for tvOS.
// Implementing it is exactly the point of this file, so the availability
// diagnostics are suppressed here and nowhere else.
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wunavailable-declarations"
#pragma clang diagnostic ignored "-Wunguarded-availability"
#pragma clang diagnostic ignored "-Wunguarded-availability-new"

@implementation UIHoverGestureRecognizer

- (instancetype)initWithTarget:(id)target action:(SEL)action {
    TVPORT_SHIM_CALL("UIHoverGestureRecognizer -initWithTarget:action:", "STUB");
    return [super initWithTarget:target action:action];
}

- (instancetype)init {
    TVPORT_SHIM_CALL("UIHoverGestureRecognizer -init", "STUB");
    return [super init];
}

// Never recognize: UIGestureRecognizer starts in ...StatePossible and this
// never moves it, matching what the iOS header says iOS does.
- (void)reset {
    TVPORT_SHIM_CALL("UIHoverGestureRecognizer -reset", "STUB");
    [super reset];
}

// Values the iOS header documents for a device with no hovering input.
- (CGFloat)zOffset {
    TVPORT_SHIM_CALL("UIHoverGestureRecognizer -zOffset", "STUB -> 0");
    return 0;  // "Will always return 0 for devices that don't support z offset"
}

- (CGFloat)altitudeAngle {
    TVPORT_SHIM_CALL("UIHoverGestureRecognizer -altitudeAngle", "STUB -> 0");
    return 0;  // "0 is returned for devices that don't support altitude"
}

- (CGFloat)rollAngle {
    TVPORT_SHIM_CALL("UIHoverGestureRecognizer -rollAngle", "STUB -> 0");
    return 0;  // "Devices that do not support roll angle will always return 0"
}

- (CGFloat)azimuthAngleInView:(UIView *)view {
    TVPORT_SHIM_CALL("UIHoverGestureRecognizer -azimuthAngleInView:", "STUB -> 0");
    return 0;  // "0 is returned for devices that don't support azimuth"
}

- (CGVector)azimuthUnitVectorInView:(UIView *)view {
    TVPORT_SHIM_CALL("UIHoverGestureRecognizer -azimuthUnitVectorInView:", "STUB -> zero");
    CGVector v = {0, 0};
    return v;  // "An empty vector is returned for devices that don't support azimuth"
}

@end

#pragma clang diagnostic pop
