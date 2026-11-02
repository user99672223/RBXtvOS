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
//  export it. The SDK does ship UIKit/UIHoverGestureRecognizer.h, but no
//  @interface survives its availability guards, so nothing can be compiled
//  against it there either.
//
//  Contract (source 3), iOS SDK 26.2,
//  System/Library/Frameworks/UIKit.framework/Headers/UIHoverGestureRecognizer.h:22
//
//      UIKIT_EXTERN API_AVAILABLE(ios(13.0)) API_UNAVAILABLE(watchos, tvos)
//      @interface UIHoverGestureRecognizer : UIGestureRecognizer
//
//  Referenced by (source 4): RobloxLib imports
//  _OBJC_CLASS_$_UIHoverGestureRecognizer non-weakly and eagerly. There is no
//  _OBJC_METACLASS_$_ import, so RobloxLib does not subclass it. No binary in
//  the bundle references zOffset, altitudeAngle, rollAngle,
//  azimuthAngleInView: or azimuthUnitVectorInView: in __objc_selrefs, so none
//  of those is implemented here: CLAUDE.md Phase 4 says a shim implements only
//  what Roblox actually references. Leaving them out also avoids depending on
//  CGVector, which the iOS declaration uses and whose availability on tvOS is
//  not established.
//
//  Decision: STUB.
//
//  Reason: the iOS header states outright that "On iOS, this gesture
//  recognizer doesn't recognize anything and is effectively a no-op." A
//  recognizer that stays in UIGestureRecognizerStatePossible and never
//  transitions is therefore not an approximation of iOS behaviour, it is the
//  same behaviour, and there is no hovering input device on an Apple TV in any
//  case. Subclassing UIGestureRecognizer keeps isKindOfClass:,
//  addGestureRecognizer: and the delegate protocol working.
//
//  Status: UNTESTED (no device run yet).
//
#import <UIKit/UIKit.h>

#include "shimsupport.h"

TVPORT_SHIM_LIBRARY("proxy_UIKit (UIHoverGestureRecognizer)")

// The real class is API_UNAVAILABLE(tvos), so it cannot come from the header.
// It is declared here with the superclass the iOS SDK gives, which is what the
// ObjC runtime and isKindOfClass: care about.
@interface UIHoverGestureRecognizer : UIGestureRecognizer
@end

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
// never moves it, matching what the header says iOS does.
- (void)reset {
    TVPORT_SHIM_CALL("UIHoverGestureRecognizer -reset", "STUB");
    [super reset];
}

@end
