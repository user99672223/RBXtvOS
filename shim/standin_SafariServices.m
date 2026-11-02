//
//  standin_SafariServices.m — stand-in for SafariServices, absent on tvOS
//
//  Evidence
//  --------
//  Missing on device (source 1): SafariServices.framework is not among the
//  images in the tvOS 27.2 (24K122) shared cache for AppleTV14,1.
//  Missing from the public API (source 2): absent from tvOS SDK 27.2.
//  Present on iOS (source 3): iOS SDK 26.2's SafariServices.tbd exports both
//  classes, and the SDK ships SFSafariViewController.h and
//  SFSafariViewControllerConfiguration.h (confirmed by a directory listing of
//  SafariServices.framework/Headers), which rules out a parsing error.
//
//  Contract: SFSafariViewController is a UIViewController subclass presented
//  modally to show a web page, and SFSafariViewControllerConfiguration is a
//  plain NSObject holding its options. The superclass could not be read back
//  out of the iOS headers by the first scan -- grep ran with -r and that
//  framework's Headers directory is reached through a symlink -- so UIKit's
//  UIViewController is used, which is what the class must be for Persona2's
//  `-initWithURL:configuration:` plus a modal presentation to work at all. If
//  the re-run of the header scan contradicts this, the superclass here is
//  wrong and must be corrected; that is recorded as the one UNVERIFIED point
//  in this shim.
//
//  Imported by (source 4): Persona2 imports
//  _OBJC_CLASS_$_SFSafariViewController,
//  _OBJC_CLASS_$_SFSafariViewControllerConfiguration and
//  _OBJC_METACLASS_$_SFSafariViewController, all non-weak and eager.
//
//  Selectors Persona2 references (source 4): -initWithURL:configuration: and
//  its own delegate callback -safariViewControllerDidFinish:.
//
//  Decision: STUB.
//
//  Reason: Persona2 is Roblox's identity-verification SDK and uses an
//  in-app browser to run a web flow. tvOS has no in-app browser: WebKit is
//  absent from the device too. There is nothing to reimplement against, so the
//  shim's job is to let Persona2 load and to fail the flow visibly rather than
//  silently. It presents nothing, logs loudly, and calls
//  -safariViewControllerDidFinish: on the next run-loop turn so the caller's
//  state machine completes instead of hanging on a dismissal that never comes.
//
//  Consequence worth stating plainly in REPORT.md: any Roblox flow that needs
//  this web view (identity / age verification) cannot work on tvOS.
//
//  Status: UNTESTED (no device run yet).
//
#import <UIKit/UIKit.h>

#include "shimsupport.h"

TVPORT_SHIM_LIBRARY("standin_SafariServices (SFSafariViewController, SFSafariViewControllerConfiguration)")

@interface SFSafariViewControllerConfiguration : NSObject <NSCopying>
@property (nonatomic) BOOL entersReaderIfAvailable;
@property (nonatomic) BOOL barCollapsingEnabled;
@end

@implementation SFSafariViewControllerConfiguration

- (instancetype)init {
    TVPORT_SHIM_CALL("SFSafariViewControllerConfiguration -init", "STUB");
    return [super init];
}

- (id)copyWithZone:(NSZone *)zone {
    TVPORT_SHIM_CALL("SFSafariViewControllerConfiguration -copyWithZone:", "STUB");
    SFSafariViewControllerConfiguration *c = [[[self class] alloc] init];
    c.entersReaderIfAvailable = self.entersReaderIfAvailable;
    c.barCollapsingEnabled = self.barCollapsingEnabled;
    return c;
}

@end

@interface SFSafariViewController : UIViewController
@property (nonatomic, weak) id delegate;
@property (nonatomic, readonly, copy) NSURL *initialURL;
- (instancetype)initWithURL:(NSURL *)URL;
- (instancetype)initWithURL:(NSURL *)URL configuration:(SFSafariViewControllerConfiguration *)configuration;
@end

@implementation SFSafariViewController {
    NSURL *_url;
}

- (instancetype)initWithURL:(NSURL *)URL
              configuration:(SFSafariViewControllerConfiguration *)configuration {
    TVPORT_SHIM_CALL("SFSafariViewController -initWithURL:configuration:", "STUB");
    self = [super initWithNibName:nil bundle:nil];
    if (self) {
        _url = [URL copy];
        TVLOG_W(TVLogShim, "SFSafariViewController stand-in created for %s; tvOS has "
                           "no in-app browser (WebKit is absent on the device too), so "
                           "this web flow cannot run",
                URL.absoluteString.UTF8String ?: "(nil)");
    }
    return self;
}

- (instancetype)initWithURL:(NSURL *)URL {
    TVPORT_SHIM_CALL("SFSafariViewController -initWithURL:", "STUB");
    return [self initWithURL:URL configuration:nil];
}

- (NSURL *)initialURL {
    TVPORT_SHIM_CALL("SFSafariViewController -initialURL", "STUB");
    return _url;
}

- (void)viewDidAppear:(BOOL)animated {
    [super viewDidAppear:animated];
    TVPORT_SHIM_CALL("SFSafariViewController -viewDidAppear:", "STUB -> finish immediately");
    // Complete the caller's state machine instead of leaving it waiting.
    id delegate = self.delegate;
    SEL sel = @selector(safariViewControllerDidFinish:);
    __weak __typeof(self) weakSelf = self;
    dispatch_async(dispatch_get_main_queue(), ^{
        __typeof(self) strongSelf = weakSelf;
        if (!strongSelf) return;
        if ([delegate respondsToSelector:sel]) {
            void (*fn)(id, SEL, id) = (void (*)(id, SEL, id))[delegate methodForSelector:sel];
            fn(delegate, sel, strongSelf);
        } else {
            [strongSelf dismissViewControllerAnimated:YES completion:nil];
        }
    });
}

@end
