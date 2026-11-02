# Reading the class dumps in this directory

These come from `ipsw dyld macho <image> --objc` over the device's dyld shared
cache for tvOS 27.2 (24K122). Two things to know before trusting a line.

## Superclasses are only resolved within the same image

`gc-cursor-classes.txt` reports

    @interface GCEventViewController : NSObject {

but tvOS SDK 27.2's `GCEventViewController.h:20` declares

    API_AVAILABLE(macos(10.11), ios(9.0), tvos(9.0))
    @interface GCEventViewController : UIViewController

The dump is not evidence that the header is wrong. `UIViewController` lives in
UIKitCore, a *different* image, and the tool falls back to `NSObject` when a
superclass reference does not resolve inside the image being dumped. The
same file shows `GCMicroGamepad : GCPhysicalInputProfile` resolved correctly,
because that superclass is in GameController itself.

Independent confirmation that the header is right: `cursor/TVPortCursor.m`
subclasses `GCEventViewController` and uses `self.view`, which only exists on
`UIViewController`, and it compiles against the tvOS 27.2 SDK.

So: **for a superclass, the SDK header is the authority; use these dumps for
ivar layout and method presence.**

## Context lines come from the whole dump, not just the class

`grep` context is applied to the original multi-megabyte dump and the kept
lines are concatenated in their original order, so the lines immediately
surrounding a match in these files can belong to an unrelated class. Find the
`@interface <Name>` line itself and read forward from there.

## What each file settled

| File | Result |
|---|---|
| `uitouch-ivars.txt` | `UITouch` layout. **No `_view` ivar**; the view is in `_cachedResponderView`, the responder in `_responder`. `_locationInWindow`, `_previousLocationInWindow`, `_window`, `_phase`, `_tapCount`, `_timestamp` all present. Fixed a real bug in `TVPortTouchSynth.m`. |
| `uievent-ivars.txt` | `UIEvent : NSObject`, with `_gsEvent`, `_hidEvent`, `_timestamp`, `_eventEnvironment`. Informational only — the touch synthesiser passes `nil` for the event. |
| `uidatepicker-class.txt` | `UIDatePicker : UIControl <UIPickerViewScrollTesting, NSCoding>` — present on the device, cross-checking the nib-class finding. |
| `gc-cursor-classes.txt` | `GCController`, `GCEventViewController` and `GCMicroGamepad` all exist on the device (see the superclass caveat above). |
| `dyld-version.txt` | `dyld-27104`. Recorded in `evidence/dyld_reexport.md`. |
| `objc-version.txt` | no version suffix found; objc4 revision UNVERIFIED. |
| `metal-newlibrarywithdata.txt` | the device's Metal exports `newLibraryWithData` and the AIR machinery (`MTLAirEntry`, `MTLObjectDeserializerAIR`, `MultiLevelAirntBinaryCache`, `ShouldCreateAIRVersion`). Confirms the API exists; says nothing about whether it accepts an iOS-targeted library, which is the actual open question. |
| `coreui-deployment-platform.txt` | strings only; does not settle whether CoreUI refuses an `ios`-tagged catalog. |
