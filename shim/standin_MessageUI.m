//
//  standin_MessageUI.m — stand-in for MessageUI, absent on tvOS
//
//  Evidence
//  --------
//  Missing on device (source 1): /System/Library/Frameworks/MessageUI.framework/
//  MessageUI is not among the 196 images resolved from the tvOS 27.2 (24K122)
//  shared cache for AppleTV14,1. The framework does not exist on the device.
//  Missing from the public API (source 2): absent from tvOS SDK 27.2.
//  Present on iOS (source 3): iOS SDK 26.2 exports both classes, which rules
//  out a parsing error.
//
//  Contract (source 3), iOS SDK 26.2:
//    MessageUI.framework/Headers/MFMailComposeViewController.h
//      @interface MFMailComposeViewController : UINavigationController
//    MessageUI.framework/Headers/MFMessageComposeViewController.h
//      @interface MFMessageComposeViewController : UINavigationController
//
//  Imported by (source 4): Roblox imports _OBJC_CLASS_$_MFMailComposeViewController,
//  _OBJC_CLASS_$_MFMessageComposeViewController and
//  _OBJC_METACLASS_$_MFMailComposeViewController, all non-weak and eager.
//
//  Selectors Roblox references (source 4, __objc_selrefs):
//      +canSendMail, +canSendText
//      -setMailComposeDelegate:, -setMessageComposeDelegate:
//      -setSubject:, -setToRecipients:, -setMessageBody:isHTML:
//      -setRecipients:, -setBody:
//      and its own delegate callbacks mailComposeController:didFinishWithResult:error:
//      and messageComposeViewController:didFinishWithResult:
//
//  Decision: STUB, with +canSendMail / +canSendText returning NO.
//
//  Reason: NO is the honest and correct answer on a device with no Mail or
//  Messages app, and it is the documented way callers are meant to find that
//  out. Roblox already has to handle it, because the same call returns NO on
//  an iPhone with no mail account configured. Returning NO means Roblox never
//  presents the composer, so the setters below should not be reached at all --
//  they are implemented anyway, and logged, so that if Roblox ignores
//  +canSendMail the log says so instead of the app crashing on a missing
//  selector.
//
//  The metaclass import means Roblox may subclass MFMailComposeViewController,
//  so the superclass must really be UINavigationController.
//
//  Status: UNTESTED (no device run yet).
//
#import <UIKit/UIKit.h>

#include "shimsupport.h"

TVPORT_SHIM_LIBRARY("standin_MessageUI (MFMailComposeViewController, MFMessageComposeViewController)")

// Result codes, from MFMailComposeViewController.h / MFMessageComposeViewController.h.
typedef NS_ENUM(NSUInteger, MFMailComposeResult) {
    MFMailComposeResultCancelled = 0,
    MFMailComposeResultSaved,
    MFMailComposeResultSent,
    MFMailComposeResultFailed
};

typedef NS_ENUM(NSInteger, MessageComposeResult) {
    MessageComposeResultCancelled = 0,
    MessageComposeResultSent,
    MessageComposeResultFailed
};

@interface MFMailComposeViewController : UINavigationController
+ (BOOL)canSendMail;
@property (nonatomic, weak) id mailComposeDelegate;
- (void)setSubject:(NSString *)subject;
- (void)setToRecipients:(NSArray<NSString *> *)toRecipients;
- (void)setCcRecipients:(NSArray<NSString *> *)ccRecipients;
- (void)setBccRecipients:(NSArray<NSString *> *)bccRecipients;
- (void)setMessageBody:(NSString *)body isHTML:(BOOL)isHTML;
- (void)addAttachmentData:(NSData *)attachment mimeType:(NSString *)mimeType fileName:(NSString *)filename;
@end

@implementation MFMailComposeViewController

+ (BOOL)canSendMail {
    TVPORT_SHIM_CALL("MFMailComposeViewController +canSendMail", "STUB -> NO");
    return NO;
}

- (void)setMailComposeDelegate:(id)delegate {
    TVPORT_SHIM_CALL("MFMailComposeViewController -setMailComposeDelegate:", "STUB");
    _mailComposeDelegate = delegate;
}

- (void)setSubject:(NSString *)subject {
    TVPORT_SHIM_CALL("MFMailComposeViewController -setSubject:", "STUB");
}

- (void)setToRecipients:(NSArray<NSString *> *)toRecipients {
    TVPORT_SHIM_CALL("MFMailComposeViewController -setToRecipients:", "STUB");
}

- (void)setCcRecipients:(NSArray<NSString *> *)ccRecipients {
    TVPORT_SHIM_CALL("MFMailComposeViewController -setCcRecipients:", "STUB");
}

- (void)setBccRecipients:(NSArray<NSString *> *)bccRecipients {
    TVPORT_SHIM_CALL("MFMailComposeViewController -setBccRecipients:", "STUB");
}

- (void)setMessageBody:(NSString *)body isHTML:(BOOL)isHTML {
    TVPORT_SHIM_CALL("MFMailComposeViewController -setMessageBody:isHTML:", "STUB");
}

- (void)addAttachmentData:(NSData *)attachment mimeType:(NSString *)mimeType
                 fileName:(NSString *)filename {
    TVPORT_SHIM_CALL("MFMailComposeViewController -addAttachmentData:mimeType:fileName:", "STUB");
}

// If Roblox ignores +canSendMail and presents this anyway, tell the delegate
// the compose was cancelled rather than leaving it waiting forever on a
// callback that will never arrive.
- (void)viewDidAppear:(BOOL)animated {
    [super viewDidAppear:animated];
    TVPORT_SHIM_CALL("MFMailComposeViewController -viewDidAppear: (presented despite canSendMail=NO)",
                     "STUB -> report cancelled");
    TVLOG_W(TVLogShim, "MFMailComposeViewController was presented even though "
                       "+canSendMail returned NO; reporting Cancelled to the delegate");
    id delegate = _mailComposeDelegate;
    SEL sel = @selector(mailComposeController:didFinishWithResult:error:);
    if ([delegate respondsToSelector:sel]) {
        void (*fn)(id, SEL, id, MFMailComposeResult, NSError *) =
            (void (*)(id, SEL, id, MFMailComposeResult, NSError *))
                [delegate methodForSelector:sel];
        fn(delegate, sel, self, MFMailComposeResultCancelled, nil);
    } else {
        [self dismissViewControllerAnimated:animated completion:nil];
    }
}

@end

@interface MFMessageComposeViewController : UINavigationController
+ (BOOL)canSendText;
+ (BOOL)canSendAttachments;
+ (BOOL)canSendSubject;
@property (nonatomic, weak) id messageComposeDelegate;
- (void)setRecipients:(NSArray<NSString *> *)recipients;
- (void)setBody:(NSString *)body;
- (void)setSubject:(NSString *)subject;
@end

@implementation MFMessageComposeViewController

+ (BOOL)canSendText {
    TVPORT_SHIM_CALL("MFMessageComposeViewController +canSendText", "STUB -> NO");
    return NO;
}

+ (BOOL)canSendAttachments {
    TVPORT_SHIM_CALL("MFMessageComposeViewController +canSendAttachments", "STUB -> NO");
    return NO;
}

+ (BOOL)canSendSubject {
    TVPORT_SHIM_CALL("MFMessageComposeViewController +canSendSubject", "STUB -> NO");
    return NO;
}

- (void)setMessageComposeDelegate:(id)delegate {
    TVPORT_SHIM_CALL("MFMessageComposeViewController -setMessageComposeDelegate:", "STUB");
    _messageComposeDelegate = delegate;
}

- (void)setRecipients:(NSArray<NSString *> *)recipients {
    TVPORT_SHIM_CALL("MFMessageComposeViewController -setRecipients:", "STUB");
}

- (void)setBody:(NSString *)body {
    TVPORT_SHIM_CALL("MFMessageComposeViewController -setBody:", "STUB");
}

- (void)setSubject:(NSString *)subject {
    TVPORT_SHIM_CALL("MFMessageComposeViewController -setSubject:", "STUB");
}

- (void)viewDidAppear:(BOOL)animated {
    [super viewDidAppear:animated];
    TVPORT_SHIM_CALL("MFMessageComposeViewController -viewDidAppear: (presented despite canSendText=NO)",
                     "STUB -> report cancelled");
    TVLOG_W(TVLogShim, "MFMessageComposeViewController was presented even though "
                       "+canSendText returned NO; reporting Cancelled to the delegate");
    id delegate = _messageComposeDelegate;
    SEL sel = @selector(messageComposeViewController:didFinishWithResult:);
    if ([delegate respondsToSelector:sel]) {
        void (*fn)(id, SEL, id, MessageComposeResult) =
            (void (*)(id, SEL, id, MessageComposeResult))[delegate methodForSelector:sel];
        fn(delegate, sel, self, MessageComposeResultCancelled);
    } else {
        [self dismissViewControllerAnimated:animated completion:nil];
    }
}

@end
