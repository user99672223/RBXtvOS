# Platform-specific bundle resources (Phase 1)

Source data: `scripts/01_inventory.py`, `scripts/carinfo.py`, `scripts/nibarchive.py` run against
Roblox 2.745.1138. Each item stays **UNVERIFIED** until a source in CLAUDE.md "Evidence sources"
settles it. The verdict column is updated as evidence arrives (see `evidence/shims.md` and REPORT.md).

## 1. Metal libraries

| File | Header (MTLB) | Notes | Status |
|---|---|---|---|
| `Frameworks/RobloxLib.framework/default.metallib` (12,284 B) | `MTLB`, platform field `0x0001`, file version 2.4, library type 0, **target-OS byte 0 (unspecified)**, OS version 0.0 | Built by Xcode 26.3 for iOS. No OS string in the header. | UNVERIFIED |
| `Frameworks/RobloxLib.framework/shaders/shaders_metal_ios.pack` (16.5 MB) | Roblox container `RBXS` v11 holding **1,648 embedded MTLB libraries** (first: platform `0x0001`, version 2.2) | Roblox's own shader pack, loaded by RobloxLib at runtime (it presumably calls `newLibraryWithData:`). The file name says it is for iOS. | UNVERIFIED |

Question: does tvOS 27's Metal (`MTLDevice newLibraryWithData:` / `newDefaultLibrary`) accept an AIR
library whose header leaves the target OS unspecified and that was compiled with the iOS triple?
The Apple TV 4K 3rd gen has the A15 GPU (Apple GPU family 8), the same as the iPhone 13, so the GPU
backend compiler is the same family.
- Evidence planned: disassemble the device's Metal / MTLCompiler library-loading path for a platform
  check (source 1, `evidence-query` workflow); Apple's documentation on metallib portability (source 7).
- **If tvOS rejects them**, the only real fix is to rebuild the shaders for tvOS. That needs Roblox's
  shader sources, which are not in the bundle. The compiled AIR would also have to go to the macOS
  runner to be re-linked. → **That would be a STOP point** (Roblox assets leaving the session). It is
  not triggered now, because nothing yet shows rejection. RobloxLib also links OpenGLES, which exists
  on tvOS, but whether Roblox still has a GL renderer is UNVERIFIED.

## 2. Compiled asset catalogs (`Assets.car`)

| Catalog | Platform metadata | Renditions by idiom | Status |
|---|---|---|---|
| `Assets.car` (main) | `deploymentPlatform=ios`, `deploymentPlatformVersion=14.0`, CoreUI 973.1 (Xcode 26.3) | universal 327, phone 44, pad 42, marketing 4 | UNVERIFIED |
| `PlugIns/RBWidgetExtension.appex/Assets.car` | ios 16.0 | universal 42, phone 2, pad 2 | n/a (extension removed) |
| `Frameworks/Persona2.framework/Assets.car` | ios 13.0, CoreUI 916.1 | universal 97 | UNVERIFIED |

- Most renditions use the `universal` idiom. tvOS CoreUI should select those for the `tv` idiom
  (fallback order tv → universal). The phone/pad-only renditions are the iOS app icons (AppIcon*,
  `marketing` = 1024 pt store icon). **UNVERIFIED** until checked against the device's CoreUI
  (idiom fallback, and whether it refuses a catalog whose `deploymentPlatform` is `ios`).
- **App icon:** tvOS uses a layered "App Icon & Top Shelf Image" brand asset compiled into the
  catalog. The iOS catalog has none, so the Home screen will show a placeholder icon. Building a tvOS
  icon means compiling an asset catalog with `actool` on the macOS runner. Merging it into Roblox's
  `Assets.car` would mean sending Roblox's catalog to the runner, which is not allowed (STOP). The
  plan keeps the iOS catalog and accepts the placeholder icon. That is cosmetic and should not block
  install or launch (UNVERIFIED).
- `CFBundleIcons` / `CFBundleIcons~ipad` (iOS icon dictionaries, alternate icons) are removed from
  Info.plist in Phase 5 (iOS-only icon keys).

## 3. Compiled nibs (36 `NIBArchive` files, all at the main bundle root)

Classes instantiated across all nibs (from the NIBArchive class table):
`NSLayoutConstraint, UIBackgroundConfiguration, UIBarButtonItem, UIButton, UIButtonConfiguration,
UICollectionView, UICollectionViewFlowLayout, UIDatePicker, UIImageView, UILabel, UILayoutGuide,
UINavigationBar, UINavigationItem, UIPageControl, UIProgressView, UIStackView, UITapGestureRecognizer,
UITextField, UITextView, UIView, UIViewController`, plus private archive helpers (`UIClassSwapper`,
`UIProxyObject`, `UIRuntime*Connection`, `UIImageNibPlaceholder`, `UIButtonContent`, `UITapRecognizer`,
`_UISystemBackgroundView`, `_UITableViewCellSeparatorView`, `_UITraitStorageList`,
`_UIRelationshipTraitStorage(Record)`, `UITableViewCellContentView`, `UIRuntimeAccessibilityConfiguration`,
`UINibKeyValuePair`) and Foundation value classes.

**RESOLVED against the device's export lists (source 1, tvOS 27.2 / 24K122).**
All 21 instantiated classes and all 14 private archive helpers are present, so
no nib should fail for a missing class. Note why none of them appeared in
`missing.json`: a nib resolves classes **by name at runtime** through
`NSClassFromString`, so nothing imports `_OBJC_CLASS_$_<name>` and neither the
import tables nor the Phase 5 resolution check can see them. They had to be
checked directly.

| Class | Device (24K122) | tvOS SDK 27.2 | Provided by |
|---|---|---|---|
| `UIDatePicker` | PRESENT | **public** (`UIKit.tbd` exports it) | UIKitCore |
| `UIProgressView`, `UIPageControl` | PRESENT | — | UIKitCore |
| `NSLayoutConstraint` | PRESENT | — | **Foundation**, not UIKit |
| `UIClassSwapper`, `UIProxyObject`, `UITapRecognizer`, `UIButtonContent`, `_UISystemBackgroundView`, `_UITableViewCellSeparatorView`, `UIRuntimeAccessibilityConfiguration`, `UINibKeyValuePair`, `UIImageNibPlaceholder`, `UITableViewCellContentView`, `_UITraitStorageList` | PRESENT | private | UIKitCore |
| every other class listed above | PRESENT | — | UIKitCore / Foundation |

Original Phase 1 candidate list, kept for the record:

| Class | Used by | Status |
|---|---|---|
| `UIDatePicker` | `RBBirthdayPickerController.nib` (sign-up birthday) | UNVERIFIED, expected missing on tvOS |
| `UIProgressView` | `RBGoogleAdOverlayViewController.nib` | UNVERIFIED |
| `UIPageControl` | `RBWelcomeViewController.nib` | UNVERIFIED |
| `UITapRecognizer`, `UIButtonContent`, `_UISystemBackgroundView`, `_UITableViewCellSeparatorView` (private) | several | UNVERIFIED |
| Every other class above | | checked automatically in Phase 2 |

`LaunchScreen.nib` (`UILaunchStoryboardName = LaunchScreen`) uses only `UIViewController`, `UIView`,
`UIImageView`, `UIColor`, `NSLayoutConstraint`. Whether tvOS honours `UILaunchStoryboardName` is UNVERIFIED.

## 4. Other resources

- `Frameworks/RobloxLib.framework/ios/` (textures, terrain, compression dictionaries): Roblox's own
  platform-named content directory, read by RobloxLib. There is no tvOS equivalent, and RobloxLib's
  code (which we do not modify) decides the path. Expected to keep working because the binary is
  unchanged. UNVERIFIED.
- `Metadata.appintents` (App Intents metadata) is harmless on tvOS. Left in place.
- `GoogleService-Info.plist`, `PrivacyInfo.xcprivacy`: platform-neutral data.
- `UIAppFonts` (`SourceSansPro-Regular.ttf`, `GothamSSm-Bold.otf`): the font files are platform-neutral.
