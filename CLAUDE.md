# Project: Run the Roblox iOS client on tvOS (Apple TV 4K, 3rd gen)

## Goal
Take the decrypted Roblox iOS app (`.ipa`, uploaded directly to this session) and produce an **unsigned tvOS IPA** that the user signs with a **free Apple developer account** and sideloads onto an **Apple TV 4K (3rd generation, `AppleTV14,1`) running tvOS 27.x**.

This is done by:
1. Retargeting every Mach-O binary in the app from iOS to tvOS,
2. Supplying a separate shim library for every API tvOS lacks,
3. Adding a Siri Remote cursor for screens that only accept taps (gameplay uses a game controller, which Roblox supports natively),
4. Packaging an unsigned IPA plus an entitlements file for the user's signing tool,
5. Building thorough on-device logging so the user can iterate later with a debugger from a Debian laptop.

The game's own logic is **never** modified. All new behavior lives in our own code.

## Work in one go
Run every phase below without waiting for the user, **except** at the explicit STOP points or when a step is genuinely blocked. Everything that doesn't need the real Apple TV must be finished in this run. The run ends with the deliverables listed in Phase 9.

---

## Where things run and what may go where

### Two machines
- **This Claude Code session (Linux):** owns the Roblox IPA. All analysis and all modification of Roblox's binaries happen here. Use `ipsw` (Linux build), LIEF (Python), and standard tools.
- **GitHub Actions `macos` runner (public repo, so free minutes):** anything that needs Xcode: SDK evidence extraction, compiling our shim/cursor code for tvOS, and checks using Apple's tools on **our own** binaries.

### Hard data rules
- **The Roblox IPA, its extracted bundle, and any Roblox binary or asset must never be committed, pushed, uploaded as an Actions artifact, or printed in workflow logs.** The repo is public. Put `input/`, `work/`, and `out/` in `.gitignore` from the very first commit.
- **Apple SDK files and system binaries must not be committed.** Derived text (symbol lists, availability results, short header excerpts quoted in the evidence log) is fine.
- Derived Roblox metadata (lists of imported symbols, selector names, linked library names) may be committed under `evidence/`.

### Moving data between the session and the runner
- The session triggers workflows with `gh workflow run` and waits with `gh run watch`.
- Workflow results go back by **committing to an `outputs` branch** (text evidence, compiled shim dylibs, dSYMs), which the session then fetches with git. Prefer this over artifact downloads, which may be blocked by this session's network allowlist.
- If this session can't reach Apple's download servers, the OTA download and extraction (Evidence source 1) also run in Actions. Only derived text is committed, never the cache itself. For targeted disassembly of system frameworks, use an `evidence-query` workflow that takes an `ipsw` command as input and commits its text output.

---

## Evidence sources (in order of authority)
Every decision about what is missing, what to shim, and how a shim behaves must be backed by one of these. Never assert from memory that an API is or isn't available on tvOS. If no evidence is found, mark it **UNVERIFIED** and list it in the final report.

1. **The device's own system libraries.** The dyld shared cache from Apple's OTA for `AppleTV14,1` at the target tvOS build (workflow input `tvos_build`; default: the latest signed tvOS 27.x build for `AppleTV14,1`; record the exact build used). Extract with `ipsw`. This is the authority on what exists **on the device**, public and private, and the source for class dumps and private API (instead of community header dumps, which are not used).
2. **The tvOS SDK** in the Xcode on the macOS runner whose tvOS SDK major version matches the device (27). Headers (`API_UNAVAILABLE(tvos)` annotations) and `.tbd` export lists define the public API. Record the Xcode version and SDK version from `xcodebuild -showsdks`.
3. **The iOS SDK Roblox was built against.** Read `DTSDKName`/`DTXcode` from Roblox's `Info.plist` and the SDK version in its `LC_BUILD_VERSION`. Use the closest available iOS SDK on the runner and record any mismatch. This is the authority for the **contract** of classes and functions missing on tvOS (superclass, protocols, method signatures, types), since tvOS headers don't declare them at all.
4. **Roblox's own binary metadata.** Imports, linked libraries, Objective-C class references, **selector references**, and method type encodings. A shim implements only what Roblox actually references, not entire classes.
5. **Apple's open-source dyld and objc4** from `github.com/apple-oss-distributions`, at the versions shipped with the target tvOS (match via the version strings in the device's dyld and libobjc). The authority on loading mechanics: platform checks, two-level-namespace binding, re-exports, weak linking, class realization.
6. **The iOS shared cache** (iPhone OTA at the iOS version matching source 3). Used only for shims marked REIMPLEMENT, to see how iOS actually behaves.
7. **Apple's developer documentation.** Intended behavior and availability notes; lower authority than sources 1–3 when they disagree.
8. **Runtime observations on the Apple TV**, gathered later by the user through the logging built in Phase 7.

---

## Repository layout
```
.github/workflows/   sdk-evidence.yml, device-evidence.yml, evidence-query.yml, build-shims.yml
input/               Roblox IPA (gitignored, never leaves the session)
work/                unpacked + patched bundle (gitignored)
out/                 final unsigned IPA (gitignored)
evidence/
  environment.md     Xcode/SDK versions, tvOS build, iOS SDK Roblox used, tool versions
  imports/           per-binary imports, linked libraries, selector refs
  exports/           tvOS export lists (from device cache and SDK)
  missing.json       computed shim list
  runtime_lookups.md dynamic class/symbol lookups found in strings
  resources.md       bundle resources that may be platform-specific (see Phase 1)
  shims.md           evidence log, one entry per missing item
shim/                proxy and stand-in libraries (source)
cursor/              Siri Remote cursor module (source)
tvlog/               logging module (source)
scripts/             every step as a re-runnable script
REPORT.md            final report (Phase 9)
```
**Everything must be scripted.** Roblox updates often and forces old clients to update, so the whole pipeline must re-run against a new IPA with minimal manual work.

---

## Phase 1: Inventory the bundle (session)
- Copy the uploaded IPA into `input/`, unpack into `work/`. List every Mach-O: main executable, everything in `Frameworks/`, app extensions in `PlugIns/`. Record each file's architecture and `LC_BUILD_VERSION`.
- **App extensions:** check whether tvOS supports each extension type (evidence: docs, SDK). Remove unsupported ones; log each removal.
- Record `Info.plist` platform keys (`MinimumOSVersion`, `UIDeviceFamily`, `CFBundleSupportedPlatforms`, `DTPlatformName`, `DTSDKName`, `UIRequiredDeviceCapabilities`) and the original entitlements.
- **Platform-specific resources** (record in `evidence/resources.md`, each marked UNVERIFIED until evidence settles it):
  - `.metallib` files: which platform each targets, and whether tvOS's Metal accepts an iOS-targeted library.
  - `Assets.car` / compiled asset catalogs: whether tvOS's CoreUI loads an iOS-compiled catalog, and that tvOS requires its own app icon format.
  - Compiled storyboards/nibs: any classes they instantiate that are missing on tvOS (these go into the shim list).

**Any resource that can only be fixed by sending Roblox's assets to the macOS runner is a STOP point. Report it to the user instead of doing it.**

## Phase 2: Collect evidence and compute the shim list
For **every** Mach-O in the bundle:
1. Linked libraries, imported symbols (including `_OBJC_CLASS_$_…`), selector references → `evidence/imports/<binary>.*`
2. tvOS exports for each linked framework from **source 1** (device cache), cross-checked with **source 2** (SDK `.tbd`) → `evidence/exports/`
3. Compute `evidence/missing.json`:
   - **Missing frameworks:** linked but absent on the device.
   - **Missing symbols:** imported from a framework that exists on tvOS but doesn't export that symbol there.
   - For each missing ObjC class: the selectors Roblox references on it (source 4).
   - Confirm each missing item exists in the iOS SDK (source 3), to rule out parsing errors.
   - Flag symbols present in the device cache but **absent from the tvOS SDK** (private on tvOS) separately. They link fine but have no public contract.
4. **Runtime lookups:** scan strings for names used with `NSClassFromString`, `objc_getClass`, `dlopen`, `dlsym`, and check each against source 1 → `evidence/runtime_lookups.md`.
5. **Storage:** find where Roblox writes files and check against Apple's tvOS local-storage documentation; plan redirects where needed.
6. **Stripped entitlements:** list which features depend on entitlements a free account can't have (push notifications, associated domains, iCloud, etc.) and find, through evidence, how Roblox behaves when they're unavailable.

Write a summary of `missing.json` into `REPORT.md`, then continue (no stop needed).

## Phase 3: The evidence log (`evidence/shims.md`)
Every missing item gets an entry **before** its shim is written:
```
### <symbol or framework>
- Imported by: <binary>
- Missing on device — evidence: <device cache check (source 1) + SDK .tbd/header (source 2)>
- Contract — evidence: <iOS SDK header path and declaration (source 3)>
- Referenced selectors / call sites: <from source 4>
- Decision: STUB | REIMPLEMENT | REDIRECT
- Reason: <why this is sufficient>
- Status: UNTESTED | CALLED AT RUNTIME | VERIFIED
```

## Phase 4: Write the shims (session) and build them (macOS runner)
- **Symbols missing from a framework that exists** (e.g. UIKit classes absent on tvOS): a **proxy dylib** that re-exports the real tvOS framework (`-reexport_library` against the SDK `.tbd`) and defines the missing symbols. Verify against dyld source (source 5) that two-level-namespace binding resolves through the re-export.
- **Missing whole frameworks:** a stand-in dylib defining only the symbols Roblox imports from it.
- **ObjC classes:** same superclass as the iOS header, so `isKindOfClass:` and subclassing still work. Implement only the referenced selectors.
- STUB = safe no-op or sensible default return, still logged.
- Compile on the macOS runner (`build-shims.yml`) for `arm64-apple-tvos<device major>` against the tvOS SDK, producing dylibs **with dSYMs**. Commit them to the `outputs` branch and fetch them into the session.

## Phase 5: Retarget and patch Roblox's binaries (session only)
For every Mach-O, using LIEF or equivalent:
- Remove the code signature.
- Set `LC_BUILD_VERSION` to platform tvOS, minimum OS = the device major version, SDK = the tvOS SDK used for the shims.
- Repoint load commands for missing or proxied frameworks to the shim dylibs in `Frameworks/`, and add a load command for the logging/cursor library.
- Verify every patched binary by re-parsing it (`ipsw macho info`), and check that every import now resolves against the device's exports plus our shims' exports. **Zero unresolved non-weak imports is the pass condition.** Write this check as a script and include its output in `REPORT.md`.

Update `Info.plist`: `UIDeviceFamily = [3]`, `CFBundleSupportedPlatforms = [AppleTVOS]`, `DTPlatformName = appletvos`, `MinimumOSVersion` to the tvOS version, remove iOS-only keys (evidence: Apple's Information Property List documentation). Log every change.

## Phase 6: Siri Remote cursor (`cursor/`)
Built into the injected library. Contract evidence comes from the GameController and UIKit headers in the tvOS SDK.
- Overlay a cursor view above the key window.
- Read the Siri Remote via GameController (`GCMicroGamepad`, clickpad as d-pad); relative movement by default.
- Keep the focus engine from consuming remote input (`GCEventViewController` / `controllerUserInteractionEnabled = NO`); verify the exact mechanism in headers.
- Play/Pause toggles the cursor; hide it automatically while a game controller is in use.
- Click → tap, in order of preference:
  1. Hit-tested view is a `UIControl` → `sendActionsForControlEvents:`.
  2. It is the engine's rendering view → deliver began/ended touches to that view's touch methods at the cursor position.
  3. Full synthesized system touch events only if 1 and 2 don't cover a screen. Any private API used must be documented in `evidence/shims.md` with source-1 evidence.
- Identify the engine's input view through targeted inspection only.

## Phase 7: Logging for on-device iteration (`tvlog/`)
The user will run the app on the Apple TV and debug from a Debian laptop using pymobiledevice3 and LLDB. Design for that:
- **`os_log`** with one subsystem (e.g. `tvport`) and categories: `shim`, `cursor`, `input`, `lifecycle`, `storage`, `crash`. This is the primary channel, readable live with `pymobiledevice3 syslog live` and filterable by subsystem.
- **A mirrored log file** in the app's Caches directory, rotated, for when live syslog isn't running.
- **Startup banner:** our build ID/git commit, Roblox version, tvOS version, device model, and the list of loaded shim libraries.
- **Shim logging:** every shim logs its first call with a short caller backtrace; later calls are counted, with the counts written periodically and on exit.
- **Crash context:** an uncaught-exception handler and signal handlers that write a final record to the log file before the crash continues (do not swallow crashes). Note in `REPORT.md` that tvOS crash reports can also be pulled with pymobiledevice3.
- **Log level** read from a key in `Info.plist` (default: verbose for this development phase).
- Ship the shim **dSYMs** in `out/` so LLDB on the laptop can symbolicate our frames.

## Phase 8: Package (session)
- Zip `work/` into `out/Roblox-tvOS-unsigned.ipa` (standard `Payload/` layout, no signature).
- Write `out/entitlements.plist` for a **free developer account**: no paid-only entitlements, keep `get-task-allow = true` so the debugger can attach (a free development profile allows it). The signing tool fills in team and app identifiers.
- Note in `REPORT.md` that free signing expires after 7 days and that the sideloading tool must support Apple TV / tvOS provisioning.

## Phase 9: Deliverables and final report
Deliver to the user: the unsigned IPA, `entitlements.plist`, the dSYMs, and `REPORT.md` containing:
1. Exact versions used (tvOS build, Xcode, tvOS SDK, iOS SDK, Roblox version, tool versions).
2. Shim summary table (item, decision, evidence, status).
3. Result of the import-resolution check (Phase 5).
4. All UNVERIFIED items and risks (especially Phase 1 resources), ordered by how likely each is to block launch.
5. **Device iteration guide** for the Debian laptop: pairing with the Apple TV, starting the tunnel, live syslog filtered to the `tvport` subsystem, starting debugserver and attaching LLDB (`platform select remote-tvos`, `process connect`), pulling crash reports, and pulling the log file. Check current pymobiledevice3 documentation for tvOS 27 support over Wi-Fi and record what was verified versus UNVERIFIED (older tvOS versions had Wi-Fi issues).
6. What to try first on the device, and what log output to send back.

## Rules
- Never modify the game's code or logic. Patches are limited to load commands, build version, code signature removal, Info.plist, entitlements, and removing unsupported extensions.
- Never claim availability or behavior without evidence; mark gaps UNVERIFIED.
- `input/` is read-only. Roblox files never leave this session (see Hard data rules).
- STOP and ask the user only when: a fix would require sending Roblox files off this session, the evidence contradicts this plan, or a decision has real trade-offs the user should make. Otherwise keep going.
