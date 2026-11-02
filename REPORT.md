# Roblox iOS → tvOS: build report

Target: **Apple TV 4K (3rd generation)**, `AppleTV14,1`, tvOS 27.x.
Deliverable: an **unsigned** tvOS IPA for you to sign with a free Apple
developer account and sideload.

> **What this report can and cannot claim.** Every binary in the bundle now
> links: the Phase 5 check below proves dyld can resolve every non-weak eager
> import and load every non-weak dependency, against the export lists of the
> actual tvOS 27.2 system libraries. That is a necessary condition for launch,
> **not a sufficient one.** A fully linked app can still fail in `+load`, in
> shader compilation, or in a tamper check. Nothing here has run on an Apple
> TV. The items under *Risks* are ranked by how likely each is to stop the app,
> and the logging in Phase 7 exists so the first run tells us which.

---

## 1. Exact versions used

| Thing | Version | How it was established |
|---|---|---|
| Roblox | 2.745.1138 (`com.roblox.robloxmobile`) | `Info.plist`; IPA SHA-256 `6756b1cf…e248`, 108,391,392 bytes |
| Roblox's build toolchain | Xcode 26.3 (17C529), SDK `iphoneos26.2` | `DTXcode`, `DTSDKName`, `LC_BUILD_VERSION` |
| **Device OS (evidence source 1)** | **tvOS 27.2, build 24K122** | latest signed build for `AppleTV14,1` per `gdmf.apple.com/v2/pmv`, posted 2026‑10‑26 |
| Device board / SoC | `AppleTV14,1_J255AP_24K122`, A15 Bionic (`t8110`) | `ipsw ota info` on the OTA |
| OTA used | full `com.apple.MobileAsset.SoftwareUpdate`, 4,966,055,936 B | SHA‑256 `b436615c…45f85c9c`, matched Apple's advertised value |
| dyld shared cache | `arm64e`, 2,709 images | extracted from that OTA |
| **tvOS SDK (source 2)** | **27.2** (SDK build 24K117), Xcode 27.2 | `xcrun --sdk appletvos`; image `xcode-27-arm64` 20261026.0314.1, macOS 27.0.1 (26A434) |
| **iOS SDK (source 3)** | **26.2**, Xcode 26.3 | `macos-26` image; exact match for Roblox's own build SDK |
| dyld on the device | **`dyld-27104`** | version string in the device's own cache |
| dyld source read (source 5) | `dyld-1378` | newest published drop; **not the same build** — see below |
| Shim build target | `arm64-apple-tvos27.0` | min OS 27.0 so it installs on any tvOS 27.x |
| `ipsw` | built from source, commit `a8f1f52` | `cgo` required |
| LIEF | 1.0.0 (`1.0.0-d05b3499b`) | session |

**Recorded mismatches.**

- The tvOS SDK is 27.2 and reports SDK build 24K117 while the device is 24K122 —
  same version, different build. The device's own export lists (source 1) are
  the authority used for every "is it there" decision, so this does not affect
  the shim list; it only means the public headers are from a slightly earlier
  build of the same release.
- The device ships **`dyld-27104`**; the re-export mechanism the proxies depend
  on was read from the newest *published* source, **`dyld-1378`**. The numbering
  was rebased onto the OS train, so this is a different build and Apple has
  published no source for it. The rule itself is not in doubt — the device's own
  UIKit depends on it right now, with UIKitCore, PrintKitUI and ShareSheet in
  its `LC_REEXPORT_DYLIB` list and 51,191 names reachable through it — but the
  claim is "mechanism observed on the device, source read against a different
  build", not "source read for this build". Detail in
  `evidence/dyld_reexport.md`.
- `libobjc.A.dylib` yields only the bare string `objc4` with no revision, so the
  objc4 version is **UNVERIFIED**. Nothing here depends on it: the shims rely on
  ordinary class realization, not on any objc4 internal.

---

## 2. What was changed, and what was not

Roblox's own code, data and assets are untouched. The patches are confined to
what CLAUDE.md permits, and every one is logged in `evidence/patch_log.md`:

- `LC_CODE_SIGNATURE` removed from all 7 remaining Mach-Os, and the stale
  on-disk `_CodeSignature/CodeResources` manifests deleted (6 of them) — they
  list hashes of files that have since been rewritten.
- `LC_BUILD_VERSION` set to platform **tvOS**, min OS **27.0**, SDK **27.2**.
  Three older binaries (`Backtrace`, `Backtrace_PLCrashReporter`,
  `libswift_Concurrency`) carry `LC_VERSION_MIN_IPHONEOS` instead; LIEF cannot
  convert one load command into another, so the command id is patched in place
  from `0x25` to `LC_VERSION_MIN_TVOS` (`0x2F`) — the two have identical layout.
- 18 load commands repointed at shim dylibs, **preserving weak linkage**
  (a `LC_LOAD_WEAK_DYLIB` stays weak).
- `@rpath/libtvport.dylib` added to each Roblox binary.
- `LC_RPATH` entries added where missing (`RobloxLib` and
  `libswift_Concurrency` had none at all) so `@rpath` reaches `Frameworks/`.
- Both app extensions removed (below).
- `Info.plist` updated (below).

**Our own dylibs are deliberately excluded from the patch pass.** The first
attempt patched them too and rewrote the UIKit proxy's `LC_REEXPORT_DYLIB` for
the real UIKit into a reference to *itself*, which would have silently
destroyed the proxy mechanism — every UIKit symbol Roblox imports resolving
into a dylib that defines one class.

### App extensions removed

| Extension | Point | Why |
|---|---|---|
| `RBNotificationServiceExtension.appex` | `com.apple.usernotifications.service` | Only runs to mutate an incoming push payload, which needs `aps-environment`; a free account cannot provision that, so it could never be invoked. |
| `RBWidgetExtension.appex` | `com.apple.widgetkit-extension` | `WidgetKit.framework` is absent from the device cache entirely and tvOS has no widgets. Removing it also dropped **46 of the 49** unresolved WidgetKit symbols. |

### Info.plist

Set: `UIDeviceFamily = [3]`, `CFBundleSupportedPlatforms = [AppleTVOS]`,
`DTPlatformName = appletvos`, `MinimumOSVersion = 27.0`.
Added: `TVPortLogLevel = verbose`, `TVPortPlatformIdentity = pad`.
Removed (iOS-only, each with its reason in `evidence/patch_log.md`):
`LSRequiresIPhoneOS`, `UIBackgroundModes`, `UIRequiredDeviceCapabilities`,
`CFBundleIcons`, `CFBundleIcons~ipad`, `UIStatusBarStyle` and the other status-bar
and orientation keys.

---

## 3. Shim summary

**28 unresolved non-weak eager symbols across 15 libraries, plus 3 libraries
with no missing symbols at all.** 19 dylibs were built (18 shims +
`libtvport`), all `platform=tvOS`, `minos=27.0`, with dSYMs. Full per-item
evidence is in `evidence/shims.md`; the decision table is
`shim/decisions.json`.

### Proxies — framework is on the device but short a symbol

Each re-exports the real framework and defines only what is missing. Verified
against dyld's source that this satisfies a two-level-namespace bind
(`evidence/dyld_reexport.md`).

| Library | Missing | Decision | Status |
|---|---|---|---|
| UIKit | `UIHoverGestureRecognizer` | STUB | UNTESTED |
| CoreFoundation | `NSURLVolumeAvailableCapacityForImportantUsageKey` | STUB | UNTESTED |
| CoreTelephony | `CTServiceRadioAccessTechnologyDidChangeNotification` | STUB | UNTESTED |
| PushKit | `PKPushTypeVoIP` | STUB | UNTESTED |
| libswiftDarwin | 7 `__swift_FORCE_LOAD_$_*` markers | STUB | UNTESTED |

**`UIHoverGestureRecognizer` is the single most important shim.** RobloxLib
imports it non-weakly and eagerly, so **RobloxLib will not load without it**.
All three sources agree it is absent: the device's UIKit closure (51,191 names,
including the UIKitCore re-export) does not have it, tvOS SDK 27.2's `UIKit.tbd`
does not export it, and iOS SDK 26.2 does. The tvOS SDK *declares* the class but
marks it `API_UNAVAILABLE(tvos)` with nothing implementing it. The iOS header
states that "On iOS, this gesture recognizer doesn't recognize anything and is
effectively a no-op", so a recognizer that never leaves
`UIGestureRecognizerStatePossible` reproduces iOS behaviour rather than
approximating it.

**CoreTelephony and PushKit are private on tvOS** — present on the device
(5,167 and 41 exports) but with no `.tbd` in the SDK, so there is no public
contract and `ld` had nothing to re-export against. Their stubs are generated
from the device's own export lists by `scripts/runner/make_tbd.py`.

### Stand-ins — framework absent from the device

| Library | What Roblox uses | Decision | Status |
|---|---|---|---|
| MessageUI | `MFMailComposeViewController`, `MFMessageComposeViewController` | STUB, `+canSendMail`/`+canSendText` → `NO` | UNTESTED |
| SafariServices | `SFSafariViewController(+Configuration)` | STUB, completes the callback | UNTESTED |
| PDFKit | `PDFDocument` (`-initWithData:`, `-pageCount`) | STUB, 0 pages | UNTESTED |
| WidgetKit | `WidgetCenter.shared`, `reloadAllTimelines` | STUB | UNTESTED |
| FileProvider | 1 autolink marker | STUB | UNTESTED |
| libswift{CallKit,CoreMIDI,DataDetection,FileProvider,PassKit} | autolink markers (+1 CallKit setter) | STUB | UNTESTED |

`+canSendMail`/`+canSendText` returning `NO` is the *correct* answer on a device
with no Mail or Messages app, and it is the documented way callers find out —
an iPhone with no mail account answers the same, so Roblox already handles it.

### Launch blockers with no missing symbols

`QuickLook`, `iAd` and `EventKitUI` are absent from the device and linked by
`Roblox` with a **non-weak** `LC_LOAD_DYLIB`, while Roblox imports **zero**
symbols from each. They never appeared in any symbol report, but dyld requires a
non-weak dependency to be loadable regardless — so the app would have died at
launch while the symbol check said everything resolved. Each now has an empty
stand-in. `scripts/05_verify.py` checks load commands independently of symbols
for exactly this reason.

### Deliberately not shimmed

- **6 lazily-bound missing symbols.** 4 flat-namespace MIG message-validation
  functions in RobloxLib's crash reporter, and 2 Swift extension members from
  `libswiftCoreData`. dyld does not resolve lazy bindings at launch, so these
  cannot block it, and the MIG four are equally unresolved in Roblox's own iOS
  build — matching iOS means leaving them alone.
- **104 weak-missing symbols and 6 absent-but-weakly-linked libraries**
  (`VisionKit`, `libswiftPhotosUI`, `libswiftCloudKit`, `DeclaredAgeRange`,
  `Social`, `MarketplaceKit`). dyld binds a missing weak import to NULL and the
  app still loads; Roblox already copes with this on iOS.

### Our own components

| Component | Decision | Notes |
|---|---|---|
| `tvlog/` | REDIRECT | `os_log` subsystem `tvport` + rotated file mirror |
| `cursor/` | REDIRECT | Siri Remote pointer; uses private `UITouch` ivars, guarded |
| `shim/platform_identity.m` | REDIRECT | reports iPadOS/iPad/Pad to the app |

---

## 4. Phase 5 result — the import-resolution check

`python3 scripts/05_verify.py`, re-reading the **patched** bundle and the built
shims from scratch and re-deriving import tables, `LC_RPATH`s and library
ordinals from the patched files:

```
device evidence : device_24K122
shim dylibs     : 19
resolved        : 7153
weak-missing    : 103  (dyld binds these to NULL; app still loads)
lazy-missing    : 4    (bound on first call, not at launch)
unloadable libs : 0  <- must be 0 to pass
BLOCKERS        : 0  <- must be 0 to pass
```

**Both pass conditions are met.** Per-binary detail and every row is in
`evidence/phase5_verify.json`.

The resolution model matches dyld: an import names both a symbol and the library
it must come from, and resolution searches that library's exports plus the
transitive closure of its `LC_REEXPORT_DYLIB` dependents — confirmed in
`dyld-1378` `Loader.cpp:3196` (`staticLink` sets `searchSelf` and
`canSearchDependents` with `searchNonReExports` false). `@rpath` is expanded
through each importing image's `LC_RPATH` plus the executable's.

### Analysis bugs found and fixed along the way

Each of these made the numbers wrong in a way that mattered, so they are listed
rather than quietly corrected:

| Bug | Effect |
|---|---|
| `@rpath` resolved as bundle-only | 423 OS Swift symbols reported missing; `/usr/lib/swift` is the first `LC_RPATH` in most of these binaries |
| library ordinals left unmapped | 201 imports in unanswerable `<ordinal N>` buckets; now 6, all explained |
| exports read from the symbol table, not the export trie | Persona2 reported 248 exports instead of 4,060; the symbols Roblox imports from it appear in its own symtab as `UNDEFINED` |
| lazy vs eager bindings not distinguished | 6 lazy imports counted as launch blockers |
| `@rpath`-named OS libraries never queried | the device was never asked about `libswiftCoreData`, which `Backtrace` links **non-weakly** — a potential launch blocker (it is present; settled) |
| SDK `.tbd` path mapping (`foo.dylib.tbd` vs `foo.tbd`) | all 44 `/usr/lib` entries looked absent from *both* SDKs — caught by the iOS cross-check that exists for this |
| header scan anchored `@interface` to line start | 3 class contracts lost; Apple puts availability macros before `@interface` on the same line |
| `grep -r` skipping symlinked SDK header dirs | SafariServices returned 0 hits for a plain substring |

---

## 5. Risks and UNVERIFIED items, most likely to block launch first

### 1. Metal shaders — the biggest risk. UNVERIFIED.

`Frameworks/RobloxLib.framework/default.metallib` (12,284 B) has an MTLB v2.4
header with the **target-OS byte unset (0)**, and
`shaders/shaders_metal_ios.pack` (16.5 MB) is a Roblox `RBXS` container holding
**1,648 embedded MTLB libraries**, the first at MTLB v2.2, platform field
`0x0001`. The file name says iOS.

Whether tvOS 27's Metal accepts an AIR library compiled with the iOS triple is
not established. In favour: the Apple TV 4K (3rd gen) has the A15 GPU, the same
family as the iPhone 13, so the GPU back-end compiler is the same. Against:
nothing confirms the loader ignores the platform field.

**If tvOS rejects them, the renderer cannot start, and this is a STOP point.**
Fixing it would mean recompiling the shaders for tvOS, which needs Roblox's
shader sources (not in the bundle) and would require sending Roblox's compiled
AIR to the macOS runner — which the hard data rules forbid. It is not triggered
yet, because nothing shows rejection. RobloxLib also links OpenGLES, which
exists on tvOS, but whether Roblox still has a working GL renderer is
UNVERIFIED.

### 2. Anti-tamper. UNVERIFIED, and structural.

Roblox probes for jailbreak artefacts by path, via `dlopen`/`dlsym`:
`/Library/MobileSubstrate/CydiaSubstrate.dylib`,
`/Library/MobileSubstrate/MobileSubstrate.dylib`, `/jb/libjailbreak.dylib`,
`/jb/amfid_payload.dylib`, `/usr/lib/libjailbreak.dylib`,
`/private/var/tmp/cydia.log`, `/private/var/mobile/Library/SBSettings/Themes`.
The main executable imports `_dlopen`, `_dlsym`, `_NSClassFromString`,
`_objc_getClass`, `_objc_lookUpClass`, `_NSProtocolFromString`, `_sel_getUid`
and `_sel_registerName` (see `evidence/runtime_lookups.md`).

This build adds an injected dylib and is re-signed — the shape of thing such
checks look for. None of that logic was modified and none of it will be; if the
client refuses to run or the service rejects it, that is the answer. Note also
that a modified client is unlikely to be permitted by Roblox's terms of service,
so treat this as a personal-device experiment.

### 3. Asset catalog and app icon. UNVERIFIED (cosmetic → moderate).

`Assets.car` is tagged `deploymentPlatform=ios`, `deploymentPlatformVersion=14.0`,
CoreUI 973.1. Of 417 renditions, 327 are `universal` and the phone/pad/marketing
ones are the iOS icons. tvOS CoreUI should select the universal ones (idiom
fallback tv → universal), but whether it refuses a catalog tagged `ios` is not
established. There is **no tvOS layered App Icon / Top Shelf brand asset**, so
the Home screen will probably show a placeholder. Building one needs `actool`
and merging it would mean sending Roblox's catalog to the runner — not allowed —
so the iOS catalog is kept and the placeholder accepted.

### 4. Nib classes — RESOLVED, no longer a risk.

36 compiled `NIBArchive` files. Phase 1 flagged `UIDatePicker` (used by
`RBBirthdayPickerController.nib`) as the class most likely to be missing, and it
never appeared in the shim list — not because it is present, but because nothing
*imports* `_OBJC_CLASS_$_UIDatePicker`. A nib resolves its classes **by name at
runtime** through `NSClassFromString`, so no import table can show them and the
Phase 5 check cannot either.

Checked directly against the device's export lists instead. **All 21 classes the
nibs instantiate are present on tvOS 27.2**, and so are all 14 of the private
archive helpers:

- `UIDatePicker` is not merely present, it is **public** — tvOS SDK 27.2's
  `UIKit.tbd` exports it.
- `UIProgressView`, `UIPageControl`, `UIButton`, `UITextField`, `UITextView`,
  `UICollectionView`, `UINavigationBar`, `UIStackView` and the rest: present.
- `NSLayoutConstraint` is present, provided by **Foundation** rather than UIKit.
- The archive internals — `UIClassSwapper`, `UIProxyObject`, `UITapRecognizer`,
  `UIButtonContent`, `_UISystemBackgroundView`,
  `_UITableViewCellSeparatorView`, `UIRuntimeAccessibilityConfiguration`,
  `UINibKeyValuePair`, `UIImageNibPlaceholder`,
  `UITableViewCellContentView`, `_UITraitStorageList`: all present.

So no nib should fail to instantiate for a missing class. Whether tvOS honours
`UILaunchStoryboardName` for the launch screen is still UNVERIFIED, but that is
cosmetic — a missing launch image, not a failure.

### 5. Storage. Known limitation.

Apple's tvOS guide states an app "can only access 500 KB of persistent storage
that is local to the device (using the `NSUserDefaults` class)" and that "all
other data must be purgeable by the operating system when space is low".
Roblox's main executable creates an **"Application Support"** directory at
startup (`RBStartupManager`, which logs "Failed to create 'Application Support'
directory" on failure) and uses `NSSearchPathForDirectoriesInDomains`,
`CFPreferences` and SQLite. RobloxLib references `/Documents/MemoryDump`,
`/tmp`, `/var/tmp`. Expect cache eviction and possibly repeated re-downloads.
No redirect is applied, because Roblox's own code chooses the paths and
redirecting them would mean modifying its logic.

### 6. Entitlements a free account cannot have.

`aps-environment`, `applesignin`, `associated-domains`, AutoFill credential
provider, `declared-age-range`, `extended-virtual-addressing`,
`increased-memory-limit`, `storekit.request-data`, communication and
time-sensitive notifications, and the `group.com.roblox.robloxmobile` app group.
Consequences: no push, no Sign in with Apple, no universal links, no App Attest,
no raised memory limit (notable for a game), and in-app purchase is unlikely to
work. Full table in `evidence/storage_entitlements.md`.

### 7. Features that cannot work on tvOS at all.

Identity / age verification (Persona2 needs `SFSafariViewController`; WebKit is
absent from the device too), mail and SMS composition, widgets, anything
cellular.

### 8. Private API in the cursor. RESOLVED, and it found a real bug.

Click-to-tap preference 2 synthesizes a `UITouch`. The device's own UIKitCore
for tvOS 27.2 declares:

```
@interface UITouch : NSObject <_UIResponderForwardableEventComponent> {
    q _phase;  Q _tapCount;  d _timestamp;
    @"UIWindow" _window;
    @"UIResponder<_UIGestureRecognizerContainer>" _responder;
    @"UIView" _cachedResponderView;
    @"UIView" _warpedIntoView;
    {CGPoint} _locationInWindow;
    {CGPoint} _previousLocationInWindow;
    ...
}
```

**There is no `_view` ivar**, and an earlier version of this code required one.
The runtime guard would have held — no crash — but the probe would have
reported `UNAVAILABLE` and preference 2 would never have worked on any screen.
That matters more than it sounds: Roblox's gameplay and most of its UI are drawn
into one engine-owned view rather than into `UIControl`s, so preference 2 is the
route that covers the majority of screens. Only source-1 evidence could surface
this, because a guard that silently disables a feature looks identical to a
feature that is working.

Fixed: the view slot now tries `_cachedResponderView`, `_view` then
`_warpedIntoView`, and `_responder` is set as well (a `UIView` is a
`UIResponder`, and `-[UITouch view]` derives from the responder on this UIKit).
Every other name used is confirmed present on the device. Preferences 1
(`UIControl`) and 3 (gesture recognizer) use no private API. The startup banner
prints the probe result, so the first device log states which ivars were found.

---

## 6. Device iteration guide (Debian laptop)

Install:

```bash
python3 -m pip install --upgrade pymobiledevice3
```

### Pair with the Apple TV

tvOS has no USB port on the 3rd-gen Apple TV 4K, so everything is over Wi-Fi.
Put the Apple TV and the laptop on the same network.

```bash
pymobiledevice3 remote browse
```

```bash
pymobiledevice3 lockdown pair --network
```

Confirm the pairing prompt on the TV.

### Start the tunnel (needed for anything RSD-based on tvOS 17+)

```bash
sudo pymobiledevice3 lockdown start-tunnel
```

Leave it running; it prints an RSD address and port that later commands use.

### Live log, filtered to our subsystem

```bash
pymobiledevice3 syslog live --match tvport
```

Categories: `shim`, `cursor`, `input`, `lifecycle`, `storage`, `crash`.

### Install and launch

Use your signing/sideloading tool for the install (it must support tvOS
provisioning). Then:

```bash
pymobiledevice3 developer dvt launch com.roblox.robloxmobile
```

### Attach a debugger

```bash
pymobiledevice3 developer debugserver start-server
```

Then in LLDB, using the host and port it printed:

```
platform select remote-tvos
process connect connect://<host>:<port>
```

Add the shipped dSYMs so our frames symbolicate:

```
settings append target.debug-file-search-paths /path/to/out/dSYMs
```

### Pull crash reports and the log file

```bash
pymobiledevice3 crash pull ./crashes
```

```bash
pymobiledevice3 afc pull Library/Caches/tvport/tvport.log ./tvport.log
```

> **UNVERIFIED:** these invocations are from pymobiledevice3's current
> documentation and have **not** been run against a tvOS 27.2 device in this
> work. Earlier tvOS releases had known problems with Wi-Fi-only
> `remote`/tunnel operation, and exact subcommand spellings move between
> pymobiledevice3 releases — check `--help` on the version you install. The
> `afc` pull path assumes the app container is reachable; if it is not, rely on
> the live syslog, which needs no file access.

---

## 7. What to try first, and what to send back

In order:

1. **Does it install and launch at all?** Start the live syslog *before*
   launching. The first thing you should see is the `tvport` startup banner:
   our build id, the Roblox version, the tvOS version, the device model, and
   the list of loaded shim libraries. **If you see the banner, dyld linked
   everything and the shims loaded** — that is the single most useful data
   point.
2. **Does it get past the splash screen to a rendered frame?** This is the
   Metal question (risk 1). Watch for Metal or shader errors near the banner.
3. **Does the cursor appear?** Press **Play/Pause** to toggle it. Move with the
   clickpad, click with a clickpad press. The log records which route each
   click took (`UIControl actions`, `synthesized touches`, `tap gesture
   recognizer`, or none).
4. **Can you sign in?**
5. **Can you join a game?** Pair a game controller first — the cursor hides
   itself when one is in use, and Roblox supports controllers natively.

Please send back:

- the full `tvport` syslog from launch (or `out/dSYMs`-symbolicated crash
  reports if it dies),
- the **startup banner** verbatim,
- every `FIRST CALL` line — these say which shims are actually being reached,
- the periodic **shim call counts**,
- anything at `error` level, especially from the `crash` category,
- the `platform identity shim` line, which reports how many of the 5 overrides
  applied and what the real tvOS values were.

Each of those turns an `UNTESTED` status in `evidence/shims.md` into `CALLED AT
RUNTIME` or `VERIFIED`, and tells us which of the risks above is the real one.

---

## 8. Signing notes

- The IPA is **unsigned**; `out/entitlements.plist` holds only what a free
  personal-team development profile can be issued: `get-task-allow` plus the
  `application-identifier`, `team-identifier` and `keychain-access-groups`
  placeholders your signing tool fills in. Asking for any of Roblox's original
  paid entitlements would make signing fail rather than degrade.
- `get-task-allow` is kept **on purpose** so `debugserver` can attach.
- **Free signing expires after 7 days**, after which the app must be re-signed
  and reinstalled.
- Your sideloading tool must support **Apple TV / tvOS** provisioning; many
  iOS-only tools do not.
- The bundle identifier is unchanged (`com.roblox.robloxmobile`). Your tool will
  most likely need to rewrite it to something under your own team prefix.

---

## 9. Deliverables

| File | What |
|---|---|
| `out/Roblox-tvOS-unsigned.ipa` | the unsigned tvOS IPA, 104,592,267 B, 846 entries, `sha256 5afdb45c…2742` |
| `out/entitlements.plist` | free-account entitlements |
| `out/dSYMs/` | 19 dSYM bundles for our own code |
| `out/package_manifest.json` | sizes, SHA-256, bundle keys, Phase 5 totals |
| `REPORT.md` | this file |

Supporting evidence, all committed:

| Path | What |
|---|---|
| `evidence/shims.md` | per-item evidence log (Phase 3) |
| `evidence/missing.json` | computed shim list |
| `evidence/phase5_verify.json` | the import-resolution result |
| `evidence/patch_log.md` | every change made to the bundle |
| `evidence/dyld_reexport.md` | the proxy mechanism, checked against dyld source |
| `evidence/runtime_lookups.md` | string-based lookups, incl. the tamper probes |
| `evidence/storage_entitlements.md` | storage and entitlement analysis |
| `evidence/resources.md` | metallib / asset catalog / nib analysis |
| `evidence/inventory.md` | Phase 1 bundle inventory |
| `evidence/exports/` | device and SDK export lists |
| `evidence/device_queries/` | targeted class/ivar dumps from the device cache, with `_README.md` on how to read them without being misled |

### Re-running against a new Roblox version

Roblox forces client updates, so the whole pipeline is scripted:

```bash
scripts/00_reassemble.sh <upload_dir> && scripts/01_unpack.sh
```

```bash
python3 scripts/01_inventory.py && python3 scripts/02_imports.py && python3 scripts/02_bundle_exports.py && python3 scripts/02_make_queries.py
```

```bash
scripts/fetch_outputs.sh && python3 scripts/02_missing.py && python3 scripts/03_shims_md.py
```

```bash
python3 scripts/05_retarget.py && python3 scripts/05_verify.py && python3 scripts/08_package.py
```

The runner side (device evidence, SDK evidence, shim build) is triggered by
bumping the `nonce` in the matching file under `evidence/queries/` and pushing;
`workflow_dispatch` is unavailable on this repository because it only reaches
workflows on the default branch and this session's GitHub proxy refuses
repository-settings writes.
