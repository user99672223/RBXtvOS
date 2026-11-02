//
//  symbols.m — the one-symbol shims
//
//  Each of these libraries exists on the Apple TV (or does not) but is short
//  exactly one or two symbols Roblox imports eagerly and non-weakly. They are
//  grouped in one source file and compiled into separate dylibs by
//  scripts/build_shims.sh, because an import names its library and dyld
//  resolves it there: one dylib per shimmed library, each carrying only that
//  library's symbols.
//
//  Which symbols, and why each one is here, is recorded per section below.
//  Compile with -DTVPORT_SHIM_<NAME> to select a section.
//
#import <Foundation/Foundation.h>

#include "shimsupport.h"

// ---------------------------------------------------------------------------
// CoreFoundation proxy: _NSURLVolumeAvailableCapacityForImportantUsageKey
//
// Missing on device (source 1): the device's CoreFoundation closure for tvOS
// 27.2 (24K122) does not export it. Present on iOS (source 3): iOS SDK 26.2
// does, so this is not a parsing error.
//
// Imported by (source 4): Roblox, non-weak and eager, bound to CoreFoundation.
//
// Decision: STUB -- define the key with its documented name.
//
// Reason: this is an NSURLResourceKey, i.e. a name used to ask
// -getResourceValue:forKey:error: for the volume capacity that matters for
// "important" storage. The value of the constant is only ever a dictionary
// key. tvOS's Foundation will not recognise it, so the lookup returns nil or
// an error, which is the same thing a caller sees on a volume that cannot
// report that capacity. Roblox uses it to decide how much it may download, so
// a nil answer makes it fall back to whatever it already does when the query
// fails.
// ---------------------------------------------------------------------------
#ifdef TVPORT_SHIM_COREFOUNDATION
TVPORT_SHIM_LIBRARY("proxy_CoreFoundation (NSURLVolumeAvailableCapacityForImportantUsageKey)")

TVPORT_DEFINE_DATA(tvport_NSURLVolumeAvailableCapacityForImportantUsageKey,
                   "_NSURLVolumeAvailableCapacityForImportantUsageKey",
                   NSString *const,
                   @"NSURLVolumeAvailableCapacityForImportantUsageKey");
#endif

// ---------------------------------------------------------------------------
// CoreTelephony proxy: _CTServiceRadioAccessTechnologyDidChangeNotification
//
// Missing on device (source 1): CoreTelephony *does* exist in the tvOS 27.2
// cache -- it is one of the frameworks that is private on tvOS rather than
// absent -- but its export closure does not contain this notification name.
// Present on iOS (source 3): iOS SDK 26.2 has it.
//
// Imported by (source 4): Roblox, non-weak and eager.
//
// Decision: STUB -- define the notification name; nothing will ever post it.
//
// Reason: an Apple TV has no cellular radio, so there is no radio access
// technology to change. Roblox registers an observer for this name to notice
// when the network type changes (for bandwidth decisions). An observer
// registered for a notification nobody posts is inert, which is the correct
// outcome, and is also what happens on an iPod touch or a Wi-Fi-only iPad.
// ---------------------------------------------------------------------------
#ifdef TVPORT_SHIM_CORETELEPHONY
TVPORT_SHIM_LIBRARY("proxy_CoreTelephony (CTServiceRadioAccessTechnologyDidChangeNotification)")

TVPORT_DEFINE_DATA(tvport_CTServiceRadioAccessTechnologyDidChangeNotification,
                   "_CTServiceRadioAccessTechnologyDidChangeNotification",
                   NSString *const,
                   @"kCTServiceRadioAccessTechnologyDidChangeNotification");
#endif

// ---------------------------------------------------------------------------
// PushKit proxy: _PKPushTypeVoIP
//
// Missing on device (source 1): PushKit exists in the tvOS 27.2 cache but its
// export closure does not contain _PKPushTypeVoIP. Present on iOS (source 3).
//
// Imported by (source 4): Roblox, non-weak and eager. Roblox's entitlements
// include VoIP background mode, which a free developer account cannot have in
// any case (see evidence/storage_entitlements.md).
//
// Decision: STUB -- define the type constant.
//
// Reason: PKPushType is an NSString used to register for a class of push. The
// registration will fail or be ignored without the aps-environment
// entitlement, which a free account cannot provision, so the constant only
// needs to exist for the binary to load. Its documented value is "PKPushTypeVoIP".
// ---------------------------------------------------------------------------
#ifdef TVPORT_SHIM_PUSHKIT
TVPORT_SHIM_LIBRARY("proxy_PushKit (PKPushTypeVoIP)")

TVPORT_DEFINE_DATA(tvport_PKPushTypeVoIP, "_PKPushTypeVoIP",
                   NSString *const, @"PKPushTypeVoIP");
#endif

// ---------------------------------------------------------------------------
// WidgetKit stand-in: WidgetCenter
//
// Missing on device (source 1): WidgetKit.framework is absent from the tvOS
// 27.2 cache. Present on iOS (source 3).
//
// Imported by (source 4): after the widget extension is removed, only three
// WidgetKit symbols remain, all from Roblox's main binary:
//     _$s9WidgetKit0A6CenterC6sharedACvgZ        WidgetCenter.shared getter
//     _$s9WidgetKit0A6CenterC18reloadAllTimelinesyyFTj   method dispatch thunk
//     _$s9WidgetKit0A6CenterCMa                  type metadata accessor
// The other 46 belonged to the extension.
//
// Decision: STUB.
//
// Reason: Roblox calls WidgetCenter.shared.reloadAllTimelines() to tell the
// home-screen widget its data changed. With the widget extension removed there
// is no widget to reload, and tvOS has no home-screen widgets at all, so doing
// nothing is the correct behaviour rather than a degradation.
//
// These are Swift ABI entry points, so they are defined with explicit asm
// names and C signatures rather than written in Swift:
//   * the `shared` getter returns the singleton, so it returns a pointer to a
//     static block of storage that is never dereferenced by anything but our
//     own thunk;
//   * the dispatch thunk takes that pointer and does nothing;
//   * the metadata accessor is the risky one. Nothing here can synthesise
//     real Swift class metadata, so it logs and returns NULL. If Roblox's code
//     actually reaches it, the Swift runtime will fault on a NULL metadata
//     pointer -- the log line immediately before the crash is the signal that
//     this stub needs real work. That trade is deliberate: the alternative is
//     fabricating a metadata record, which would fail less visibly.
// ---------------------------------------------------------------------------
#ifdef TVPORT_SHIM_WIDGETKIT
TVPORT_SHIM_LIBRARY("standin_WidgetKit (WidgetCenter)")

static char tvport_widget_center_storage[64] __attribute__((aligned(16)));

void *tvport_WidgetCenter_shared_getter(void)
    __asm__("_$s9WidgetKit0A6CenterC6sharedACvgZ");
void *tvport_WidgetCenter_shared_getter(void) {
    TVPORT_SHIM_CALL("WidgetCenter.shared (getter)", "STUB");
    return tvport_widget_center_storage;
}

void tvport_WidgetCenter_reloadAllTimelines(void *self_)
    __asm__("_$s9WidgetKit0A6CenterC18reloadAllTimelinesyyFTj");
void tvport_WidgetCenter_reloadAllTimelines(void *self_) {
    TVPORT_SHIM_CALL("WidgetCenter.reloadAllTimelines()", "STUB -> no-op");
    (void)self_;
}

void *tvport_WidgetCenter_metadata_accessor(unsigned long request)
    __asm__("_$s9WidgetKit0A6CenterCMa");
void *tvport_WidgetCenter_metadata_accessor(unsigned long request) {
    TVPORT_SHIM_CALL("WidgetCenter type metadata accessor", "STUB -> NULL");
    TVLOG_E(TVLogShim, "WidgetCenter type metadata was requested (request=%lu). "
                       "This stub cannot synthesise Swift metadata and is "
                       "returning NULL; a fault in the Swift runtime after this "
                       "line means the WidgetKit stand-in needs real work.",
            request);
    return NULL;
}
#endif

// ---------------------------------------------------------------------------
// CallKit Swift overlay stand-in
//
// Missing on device (source 1): /usr/lib/swift/libswiftCallKit.dylib is absent
// from the tvOS 27.2 cache (CallKit itself exists on the device but is private
// on tvOS; its Swift overlay is not shipped).
//
// Imported by (source 4): Roblox, non-weak and eager:
//     __swift_FORCE_LOAD_$_swiftCallKit      autolink marker
//     _$sSo23CXProviderConfigurationC7CallKitE20supportedHandleTypesShySo12CXHandleTypeVGvs
//                                            setter for the Swift extension's
//                                            supportedHandleTypes property
//
// Decision: STUB.
//
// Reason: an Apple TV cannot place or receive phone calls, so a VoIP provider
// configuration has nothing to configure. The setter accepts the value and
// discards it; the marker is inert by construction.
// ---------------------------------------------------------------------------
#ifdef TVPORT_SHIM_SWIFTCALLKIT
TVPORT_SHIM_LIBRARY("standin_libswiftCallKit")

TVPORT_SWIFT_FORCE_LOAD(tvport_force_load_swiftCallKit,
                        "__swift_FORCE_LOAD_$_swiftCallKit");

void tvport_CXProviderConfiguration_setSupportedHandleTypes(void *set, void *self_)
    __asm__("_$sSo23CXProviderConfigurationC7CallKitE20supportedHandleTypesShySo12CXHandleTypeVGvs");
void tvport_CXProviderConfiguration_setSupportedHandleTypes(void *set, void *self_) {
    TVPORT_SHIM_CALL("CXProviderConfiguration.supportedHandleTypes (setter)", "STUB -> no-op");
    (void)set; (void)self_;
}
#endif

// ---------------------------------------------------------------------------
// Swift overlay autolink markers, one dylib each
//
// Swift emits one undefined `__swift_FORCE_LOAD_$_<overlay>` reference per
// imported framework, purely so the linker pulls that overlay in. Nothing ever
// reads the value. These overlays are absent from the tvOS 27.2 cache:
//     libswiftCoreMIDI, libswiftDataDetection, libswiftFileProvider,
//     libswiftPassKit
// and FileProvider.framework itself is absent too, which is where Roblox's
// __swift_FORCE_LOAD_$_swiftFileProvider reference is bound.
//
// libswiftDarwin is different and is the interesting case: the dylib *is*
// present on the device, but its export closure does not contain the seven
// per-submodule markers Roblox imports from it (swift_errno, swift_math,
// swift_signal, swift_stdio, swift_time, swiftsys_time, swiftunistd). On tvOS
// those Darwin submodule overlays are evidently folded in rather than shipped
// separately. So libswiftDarwin gets a *proxy* that re-exports the real dylib
// and adds the seven markers, while the others get stand-ins.
//
// Decision: STUB for all of them. A zero word is a faithful stand-in for a
// symbol whose only purpose is to exist at link time.
// ---------------------------------------------------------------------------
#ifdef TVPORT_SHIM_SWIFTDARWIN
TVPORT_SHIM_LIBRARY("proxy_libswiftDarwin (7 autolink markers)")
TVPORT_SWIFT_FORCE_LOAD(tvport_fl_swift_errno,    "__swift_FORCE_LOAD_$_swift_errno");
TVPORT_SWIFT_FORCE_LOAD(tvport_fl_swift_math,     "__swift_FORCE_LOAD_$_swift_math");
TVPORT_SWIFT_FORCE_LOAD(tvport_fl_swift_signal,   "__swift_FORCE_LOAD_$_swift_signal");
TVPORT_SWIFT_FORCE_LOAD(tvport_fl_swift_stdio,    "__swift_FORCE_LOAD_$_swift_stdio");
TVPORT_SWIFT_FORCE_LOAD(tvport_fl_swift_time,     "__swift_FORCE_LOAD_$_swift_time");
TVPORT_SWIFT_FORCE_LOAD(tvport_fl_swiftsys_time,  "__swift_FORCE_LOAD_$_swiftsys_time");
TVPORT_SWIFT_FORCE_LOAD(tvport_fl_swiftunistd,    "__swift_FORCE_LOAD_$_swiftunistd");
#endif

#ifdef TVPORT_SHIM_SWIFTCOREMIDI
TVPORT_SHIM_LIBRARY("standin_libswiftCoreMIDI")
TVPORT_SWIFT_FORCE_LOAD(tvport_fl_swiftCoreMIDI, "__swift_FORCE_LOAD_$_swiftCoreMIDI");
#endif

#ifdef TVPORT_SHIM_SWIFTDATADETECTION
TVPORT_SHIM_LIBRARY("standin_libswiftDataDetection")
TVPORT_SWIFT_FORCE_LOAD(tvport_fl_swiftDataDetection, "__swift_FORCE_LOAD_$_swiftDataDetection");
#endif

#ifdef TVPORT_SHIM_SWIFTFILEPROVIDER
TVPORT_SHIM_LIBRARY("standin_libswiftFileProvider")
TVPORT_SWIFT_FORCE_LOAD(tvport_fl_swiftFileProvider_overlay, "__swift_FORCE_LOAD_$_swiftFileProvider");
#endif

#ifdef TVPORT_SHIM_SWIFTPASSKIT
TVPORT_SHIM_LIBRARY("standin_libswiftPassKit")
TVPORT_SWIFT_FORCE_LOAD(tvport_fl_swiftPassKit, "__swift_FORCE_LOAD_$_swiftPassKit");
#endif

// FileProvider.framework itself: absent from the device, and the only symbol
// Roblox imports from it is the Swift overlay's autolink marker.
#ifdef TVPORT_SHIM_FILEPROVIDER
TVPORT_SHIM_LIBRARY("standin_FileProvider")
TVPORT_SWIFT_FORCE_LOAD(tvport_fl_swiftFileProvider_framework, "__swift_FORCE_LOAD_$_swiftFileProvider");
#endif
