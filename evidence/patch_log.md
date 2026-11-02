# Phase 5 patch log
target: platform tvOS, minos 27.0, sdk 27.2
injected library: @rpath/libtvport.dylib

## App extensions
removed PlugIns/RBNotificationServiceExtension.appex (com.apple.usernotifications.service): A UNNotificationServiceExtension only runs to mutate an incoming push payload, which requires the aps-environment entitlement. A free Apple developer account cannot provision that, so the extension can never be invoked; and tvOS does not host this extension point.
removed PlugIns/RBWidgetExtension.appex (com.apple.widgetkit-extension): A WidgetKit extension draws Home Screen / Lock Screen widgets. WidgetKit.framework is absent from the tvOS 27.2 device cache altogether (evidence source 1), and tvOS has no widgets, so the extension cannot load. Removing it also drops 46 of the 49 unresolved WidgetKit symbols.
removed the now-empty PlugIns directory

## Shim libraries installed into the bundle
installed Frameworks/libtvport.dylib
installed Frameworks/libtvportshim-CoreFoundation.dylib
installed Frameworks/libtvportshim-CoreTelephony.dylib
installed Frameworks/libtvportshim-EventKitUI.dylib
installed Frameworks/libtvportshim-FileProvider.dylib
installed Frameworks/libtvportshim-MessageUI.dylib
installed Frameworks/libtvportshim-PDFKit.dylib
installed Frameworks/libtvportshim-PushKit.dylib
installed Frameworks/libtvportshim-QuickLook.dylib
installed Frameworks/libtvportshim-SafariServices.dylib
installed Frameworks/libtvportshim-UIKit.dylib
installed Frameworks/libtvportshim-WidgetKit.dylib
installed Frameworks/libtvportshim-iAd.dylib
installed Frameworks/libtvportshim-libswiftCallKit.dylib
installed Frameworks/libtvportshim-libswiftCoreMIDI.dylib
installed Frameworks/libtvportshim-libswiftDarwin.dylib
installed Frameworks/libtvportshim-libswiftDataDetection.dylib
installed Frameworks/libtvportshim-libswiftFileProvider.dylib
installed Frameworks/libtvportshim-libswiftPassKit.dylib

## Info.plist
UIDeviceFamily [1, 2] -> [3]
CFBundleSupportedPlatforms ['iPhoneOS'] -> ['AppleTVOS']
DTPlatformName iphoneos -> appletvos
MinimumOSVersion 14.0 -> 27.0
removed LSRequiresIPhoneOS: iOS-only launch gate
removed UIRequiresFullScreen: iPad multitasking only
removed UISupportedInterfaceOrientations: tvOS has no device orientation
removed UISupportedInterfaceOrientations~ipad: tvOS has no device orientation
removed UIStatusBarStyle: tvOS has no status bar
removed UIViewControllerBasedStatusBarAppearance: tvOS has no status bar
removed UIStatusBarHidden: tvOS has no status bar
removed CFBundleIcons: iOS icon dictionary; tvOS uses a layered brand asset
removed CFBundleIcons~ipad: iOS icon dictionary
removed UIRequiredDeviceCapabilities: names iOS-only capabilities
removed UIBackgroundModes: the remaining modes (remote-notification, voip, fetch) need entitlements a free account cannot have, and voip has no meaning on tvOS
removed UIPrerenderedIcon: iOS icon key
added TVPortLogLevel=verbose, TVPortPlatformIdentity=pad

## Mach-O binaries

Our own dylibs are **not** patched. The runner already built them for tvOS 27.0, and running them through the repointing pass is actively harmful: the first attempt rewrote the UIKit proxy's own LC_REEXPORT_DYLIB for the real UIKit into a reference to itself, which would have left every UIKit symbol Roblox imports resolving into a dylib that defines one class. The verifier would have caught it, but the right fix is not to touch them at all.


### Frameworks/Backtrace.framework/Backtrace
- removed LC_CODE_SIGNATURE
- LC_VERSION_MIN_IPHONEOS -> LC_VERSION_MIN_TVOS, minos 9.0.0 -> 27.0, sdk 14.4.0 -> 27.2
- load command /System/Library/Frameworks/CoreFoundation.framework/CoreFoundation -> @rpath/libtvportshim-CoreFoundation.dylib
- load command /System/Library/Frameworks/UIKit.framework/UIKit -> @rpath/libtvportshim-UIKit.dylib
- added LC_LOAD_DYLIB @rpath/libtvport.dylib

### Frameworks/Backtrace_PLCrashReporter.framework/Backtrace_PLCrashReporter
- removed LC_CODE_SIGNATURE
- LC_VERSION_MIN_IPHONEOS -> LC_VERSION_MIN_TVOS, minos 9.0.0 -> 27.0, sdk 14.4.0 -> 27.2
- load command /System/Library/Frameworks/CoreFoundation.framework/CoreFoundation -> @rpath/libtvportshim-CoreFoundation.dylib
- added LC_LOAD_DYLIB @rpath/libtvport.dylib

### Frameworks/Persona2.framework/Persona2
- removed LC_CODE_SIGNATURE
- LC_BUILD_VERSION platform PLATFORMS.IOS -> TVOS, minos 13.0.0 -> 27.0, sdk 18.1.0 -> 27.2
- load command /System/Library/Frameworks/CoreFoundation.framework/CoreFoundation -> @rpath/libtvportshim-CoreFoundation.dylib
- load command /System/Library/Frameworks/CoreTelephony.framework/CoreTelephony -> @rpath/libtvportshim-CoreTelephony.dylib
- load command /System/Library/Frameworks/PDFKit.framework/PDFKit -> @rpath/libtvportshim-PDFKit.dylib
- load command /System/Library/Frameworks/SafariServices.framework/SafariServices -> @rpath/libtvportshim-SafariServices.dylib
- load command /System/Library/Frameworks/UIKit.framework/UIKit -> @rpath/libtvportshim-UIKit.dylib
- load command /usr/lib/swift/libswiftCoreMIDI.dylib -> @rpath/libtvportshim-libswiftCoreMIDI.dylib
- load command /usr/lib/swift/libswiftDarwin.dylib -> @rpath/libtvportshim-libswiftDarwin.dylib
- load command /usr/lib/swift/libswiftDataDetection.dylib -> @rpath/libtvportshim-libswiftDataDetection.dylib
- load command /usr/lib/swift/libswiftFileProvider.dylib -> @rpath/libtvportshim-libswiftFileProvider.dylib
- load command /usr/lib/swift/libswiftPassKit.dylib -> @rpath/libtvportshim-libswiftPassKit.dylib
- added LC_LOAD_DYLIB @rpath/libtvport.dylib

### Frameworks/Reaper.framework/Reaper
- removed LC_CODE_SIGNATURE
- LC_BUILD_VERSION platform PLATFORMS.IOS -> TVOS, minos 13.0.0 -> 27.0, sdk 26.2.0 -> 27.2
- load command /System/Library/Frameworks/CoreFoundation.framework/CoreFoundation -> @rpath/libtvportshim-CoreFoundation.dylib
- load command /System/Library/Frameworks/UIKit.framework/UIKit -> @rpath/libtvportshim-UIKit.dylib
- load command /usr/lib/swift/libswiftDarwin.dylib -> @rpath/libtvportshim-libswiftDarwin.dylib
- added LC_LOAD_DYLIB @rpath/libtvport.dylib
- added LC_RPATH @executable_path/Frameworks
- added LC_RPATH @loader_path/Frameworks

### Frameworks/RobloxLib.framework/RobloxLib
- removed LC_CODE_SIGNATURE
- LC_BUILD_VERSION platform PLATFORMS.IOS -> TVOS, minos 13.0.0 -> 27.0, sdk 26.2.0 -> 27.2
- load command /System/Library/Frameworks/CoreFoundation.framework/CoreFoundation -> @rpath/libtvportshim-CoreFoundation.dylib
- load command /System/Library/Frameworks/UIKit.framework/UIKit -> @rpath/libtvportshim-UIKit.dylib
- added LC_LOAD_DYLIB @rpath/libtvport.dylib
- added LC_RPATH @executable_path/Frameworks
- added LC_RPATH @loader_path/Frameworks

### Frameworks/libswift_Concurrency.dylib
- removed LC_CODE_SIGNATURE
- LC_VERSION_MIN_IPHONEOS -> LC_VERSION_MIN_TVOS, minos 7.0.0 -> 27.0, sdk 16.1.0 -> 27.2
- load command /System/Library/Frameworks/CoreFoundation.framework/CoreFoundation -> @rpath/libtvportshim-CoreFoundation.dylib
- added LC_LOAD_DYLIB @rpath/libtvport.dylib
- added LC_RPATH @executable_path/Frameworks
- added LC_RPATH @loader_path/Frameworks

### Frameworks/libtvport.dylib
- skipped: built by the shim build for tvOS; not patched

### Frameworks/libtvportshim-CoreFoundation.dylib
- skipped: built by the shim build for tvOS; not patched

### Frameworks/libtvportshim-CoreTelephony.dylib
- skipped: built by the shim build for tvOS; not patched

### Frameworks/libtvportshim-EventKitUI.dylib
- skipped: built by the shim build for tvOS; not patched

### Frameworks/libtvportshim-FileProvider.dylib
- skipped: built by the shim build for tvOS; not patched

### Frameworks/libtvportshim-MessageUI.dylib
- skipped: built by the shim build for tvOS; not patched

### Frameworks/libtvportshim-PDFKit.dylib
- skipped: built by the shim build for tvOS; not patched

### Frameworks/libtvportshim-PushKit.dylib
- skipped: built by the shim build for tvOS; not patched

### Frameworks/libtvportshim-QuickLook.dylib
- skipped: built by the shim build for tvOS; not patched

### Frameworks/libtvportshim-SafariServices.dylib
- skipped: built by the shim build for tvOS; not patched

### Frameworks/libtvportshim-UIKit.dylib
- skipped: built by the shim build for tvOS; not patched

### Frameworks/libtvportshim-WidgetKit.dylib
- skipped: built by the shim build for tvOS; not patched

### Frameworks/libtvportshim-iAd.dylib
- skipped: built by the shim build for tvOS; not patched

### Frameworks/libtvportshim-libswiftCallKit.dylib
- skipped: built by the shim build for tvOS; not patched

### Frameworks/libtvportshim-libswiftCoreMIDI.dylib
- skipped: built by the shim build for tvOS; not patched

### Frameworks/libtvportshim-libswiftDarwin.dylib
- skipped: built by the shim build for tvOS; not patched

### Frameworks/libtvportshim-libswiftDataDetection.dylib
- skipped: built by the shim build for tvOS; not patched

### Frameworks/libtvportshim-libswiftFileProvider.dylib
- skipped: built by the shim build for tvOS; not patched

### Frameworks/libtvportshim-libswiftPassKit.dylib
- skipped: built by the shim build for tvOS; not patched

### Roblox
- removed LC_CODE_SIGNATURE
- LC_BUILD_VERSION platform PLATFORMS.IOS -> TVOS, minos 14.0.0 -> 27.0, sdk 26.2.0 -> 27.2
- load command /System/Library/Frameworks/CoreTelephony.framework/CoreTelephony -> @rpath/libtvportshim-CoreTelephony.dylib
- load command /System/Library/Frameworks/QuickLook.framework/QuickLook -> @rpath/libtvportshim-QuickLook.dylib
- load command /System/Library/Frameworks/iAd.framework/iAd -> @rpath/libtvportshim-iAd.dylib
- load command /System/Library/Frameworks/PushKit.framework/PushKit -> @rpath/libtvportshim-PushKit.dylib
- load command /System/Library/Frameworks/EventKitUI.framework/EventKitUI -> @rpath/libtvportshim-EventKitUI.dylib
- load command /System/Library/Frameworks/MessageUI.framework/MessageUI -> @rpath/libtvportshim-MessageUI.dylib
- load command /System/Library/Frameworks/UIKit.framework/UIKit -> @rpath/libtvportshim-UIKit.dylib
- load command /System/Library/Frameworks/CoreFoundation.framework/CoreFoundation -> @rpath/libtvportshim-CoreFoundation.dylib
- load command /System/Library/Frameworks/FileProvider.framework/FileProvider -> @rpath/libtvportshim-FileProvider.dylib
- load command /System/Library/Frameworks/WidgetKit.framework/WidgetKit -> @rpath/libtvportshim-WidgetKit.dylib
- load command /usr/lib/swift/libswiftCallKit.dylib -> @rpath/libtvportshim-libswiftCallKit.dylib
- load command /usr/lib/swift/libswiftCoreMIDI.dylib -> @rpath/libtvportshim-libswiftCoreMIDI.dylib
- load command /usr/lib/swift/libswiftDarwin.dylib -> @rpath/libtvportshim-libswiftDarwin.dylib
- load command /usr/lib/swift/libswiftDataDetection.dylib -> @rpath/libtvportshim-libswiftDataDetection.dylib
- load command /usr/lib/swift/libswiftPassKit.dylib -> @rpath/libtvportshim-libswiftPassKit.dylib
- added LC_LOAD_DYLIB @rpath/libtvport.dylib
- added LC_RPATH @loader_path/Frameworks
