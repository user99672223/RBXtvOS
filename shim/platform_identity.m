//
//  platform_identity.m — make Roblox's own code see an iOS/iPadOS environment
//
//  Why this exists
//  ---------------
//  Roblox's UI was built for iPhone and iPad. On tvOS the same code asks UIKit
//  what it is running on and gets answers it has no layouts, assets or code
//  paths for: UIDevice.systemName returns "tvOS", and
//  UIDevice.userInterfaceIdiom returns UIUserInterfaceIdiomTV, which is an
//  idiom Roblox's build never handles. Whatever branch that selects is, at
//  best, untested and, at worst, a screen that cannot be used. Answering these
//  queries the way iPadOS would is a compatibility shim in exactly the sense
//  the rest of Phase 4 means it: our own code, standing in for behaviour this
//  platform does not provide.
//
//  Scope, deliberately narrow
//  --------------------------
//  This changes what *local* UIKit identity APIs return inside this process.
//  It does not touch Roblox's code, its anti-tamper checks, or anything that
//  talks to Roblox's servers, and it is not built to defeat attestation: App
//  Attest and push need entitlements a free developer account cannot have, so
//  those degrade on their own regardless (see evidence/storage_entitlements.md).
//
//  Info.plist itself keeps UIDeviceFamily = [3] and CFBundleSupportedPlatforms
//  = [AppleTVOS]; tvOS will not install the app otherwise, so that part cannot
//  and should not be faked.
//
//  Method
//  ------
//  Swizzling UIDevice's and UITraitCollection's getters, not replacing the
//  classes. The real implementation is kept and called, so the true tvOS value
//  is logged next to the value handed back -- the log is the record of what was
//  actually changed, and makes it obvious on device whether a given screen's
//  behaviour came from this shim.
//
//  Configuration (Info.plist):
//      TVPortPlatformIdentity   "pad" (default) | "phone" | "tv" | "off"
//  "tv" and "off" both disable the shim; "tv" reads better when the intent is
//  "report the truth".
//
//  Decision: REDIRECT.
//  Status: UNTESTED (no device run yet). The startup banner records the real
//  tvOS values, so the first device log says exactly what was overridden.
//
#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>

#import <objc/runtime.h>

#include "../tvlog/tvlog.h"

typedef NS_ENUM(NSInteger, TVPortIdentityMode) {
    TVPortIdentityOff = 0,
    TVPortIdentityPad,
    TVPortIdentityPhone,
};

static TVPortIdentityMode sMode = TVPortIdentityPad;

static NSString *sReportedSystemName = @"iPadOS";
static NSString *sReportedModel = @"iPad";
static UIUserInterfaceIdiom sReportedIdiom = UIUserInterfaceIdiomPad;

// Real implementations, kept so the truth can be logged and so nothing is lost.
static NSString *(*sOrigSystemName)(id, SEL);
static NSString *(*sOrigModel)(id, SEL);
static NSString *(*sOrigLocalizedModel)(id, SEL);
static UIUserInterfaceIdiom (*sOrigDeviceIdiom)(id, SEL);
static UIUserInterfaceIdiom (*sOrigTraitIdiom)(id, SEL);

static NSString *tvport_systemName(id self_, SEL _cmd) {
    NSString *real = sOrigSystemName ? sOrigSystemName(self_, _cmd) : @"?";
    TVLOG_SHIM("UIDevice -systemName", "REDIRECT");
    TVLOG_D(TVLogShim, "UIDevice.systemName: real=%s reported=%s",
            real.UTF8String, sReportedSystemName.UTF8String);
    return sReportedSystemName;
}

static NSString *tvport_model(id self_, SEL _cmd) {
    NSString *real = sOrigModel ? sOrigModel(self_, _cmd) : @"?";
    TVLOG_SHIM("UIDevice -model", "REDIRECT");
    TVLOG_D(TVLogShim, "UIDevice.model: real=%s reported=%s",
            real.UTF8String, sReportedModel.UTF8String);
    return sReportedModel;
}

static NSString *tvport_localizedModel(id self_, SEL _cmd) {
    TVLOG_SHIM("UIDevice -localizedModel", "REDIRECT");
    return sReportedModel;
}

static UIUserInterfaceIdiom tvport_deviceIdiom(id self_, SEL _cmd) {
    UIUserInterfaceIdiom real = sOrigDeviceIdiom ? sOrigDeviceIdiom(self_, _cmd)
                                                 : UIUserInterfaceIdiomTV;
    TVLOG_SHIM("UIDevice -userInterfaceIdiom", "REDIRECT");
    TVLOG_D(TVLogShim, "UIDevice.userInterfaceIdiom: real=%ld reported=%ld",
            (long)real, (long)sReportedIdiom);
    return sReportedIdiom;
}

static UIUserInterfaceIdiom tvport_traitIdiom(id self_, SEL _cmd) {
    UIUserInterfaceIdiom real = sOrigTraitIdiom ? sOrigTraitIdiom(self_, _cmd)
                                                : UIUserInterfaceIdiomTV;
    TVLOG_SHIM("UITraitCollection -userInterfaceIdiom", "REDIRECT");
    (void)real;
    return sReportedIdiom;
}

/// Replace one method's implementation, keeping the original. Returns NO and
/// logs if the selector is not there, so an OS change degrades to "identity
/// not overridden" instead of a crash.
static BOOL swizzle(Class cls, SEL sel, IMP replacement, void *origSlot) {
    if (!cls) {
        TVLOG_W(TVLogShim, "platform identity: class missing for %s", sel_getName(sel));
        return NO;
    }
    Method m = class_getInstanceMethod(cls, sel);
    if (!m) {
        TVLOG_W(TVLogShim, "platform identity: -[%s %s] not found; not overridden",
                class_getName(cls), sel_getName(sel));
        return NO;
    }
    IMP original = method_setImplementation(m, replacement);
    if (origSlot) *(IMP *)origSlot = original;
    return YES;
}

static TVPortIdentityMode mode_from_string(NSString *s) {
    if ([s isEqualToString:@"off"] || [s isEqualToString:@"tv"]) return TVPortIdentityOff;
    if ([s isEqualToString:@"phone"]) return TVPortIdentityPhone;
    return TVPortIdentityPad;
}

__attribute__((constructor))
static void tvport_platform_identity_init(void) {
    tvlog_init();

    @autoreleasepool {
        id raw = [[NSBundle mainBundle] objectForInfoDictionaryKey:@"TVPortPlatformIdentity"];
        if ([raw isKindOfClass:[NSString class]]) {
            sMode = mode_from_string(((NSString *)raw).lowercaseString);
        }
        const char *env = getenv("TVPORT_PLATFORM_IDENTITY");
        if (env && *env) {
            sMode = mode_from_string([@(env) lowercaseString]);
        }

        if (sMode == TVPortIdentityOff) {
            TVLOG_I(TVLogShim, "platform identity shim disabled; the app will see tvOS");
            return;
        }
        if (sMode == TVPortIdentityPhone) {
            sReportedSystemName = @"iOS";
            sReportedModel = @"iPhone";
            sReportedIdiom = UIUserInterfaceIdiomPhone;
        } else {
            sReportedSystemName = @"iPadOS";
            sReportedModel = @"iPad";
            sReportedIdiom = UIUserInterfaceIdiomPad;
        }

        Class device = NSClassFromString(@"UIDevice");
        Class traits = NSClassFromString(@"UITraitCollection");

        int applied = 0;
        applied += swizzle(device, @selector(systemName),
                           (IMP)tvport_systemName, &sOrigSystemName);
        applied += swizzle(device, @selector(model),
                           (IMP)tvport_model, &sOrigModel);
        applied += swizzle(device, @selector(localizedModel),
                           (IMP)tvport_localizedModel, &sOrigLocalizedModel);
        applied += swizzle(device, @selector(userInterfaceIdiom),
                           (IMP)tvport_deviceIdiom, &sOrigDeviceIdiom);
        applied += swizzle(traits, @selector(userInterfaceIdiom),
                           (IMP)tvport_traitIdiom, &sOrigTraitIdiom);

        TVLOG_I(TVLogShim, "platform identity shim: mode=%s, %d of 5 overrides applied "
                           "(systemName=%s model=%s idiom=%ld). "
                           "systemVersion is left alone so the real tvOS version "
                           "still appears in logs and crash reports.",
                sMode == TVPortIdentityPhone ? "phone" : "pad", applied,
                sReportedSystemName.UTF8String, sReportedModel.UTF8String,
                (long)sReportedIdiom);
    }
}
