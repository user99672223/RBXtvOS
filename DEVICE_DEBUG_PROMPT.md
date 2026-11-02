You are running on my Debian laptop. I need you to debug why a sideloaded tvOS
app fails to start on my Apple TV, and to produce an evidence bundle I can
upload to another Claude session that built the app.

Work carefully and in order. There are two hard STOP points where you must
report and wait for me.

---

# 0. What is being debugged

An **unsigned tvOS IPA** was built by porting the Roblox **iOS** client to
tvOS: every Mach-O was retargeted from iOS to tvOS, 18 small shim dylibs were
added for APIs tvOS lacks, and an injected logging/cursor library was added.

- Device: **Apple TV 4K (3rd generation)**, `AppleTV14,1`, tvOS 27.x, on the
  same Wi-Fi as this laptop. No USB (this model has no USB port).
- The IPA was signed and installed with **atvloadly** using a free Apple ID.
- Original bundle id: `com.roblox.robloxmobile`, version `2.745.1138`.
  **atvloadly very likely rewrote the bundle id** to sit under my team prefix —
  do not assume the original id, discover the installed one.
- The app **installed successfully but does not start as expected.** That is
  the whole problem. "Installed but won't run" is the symptom to explain.
- The build's `MinimumOSVersion` is `27.0`, so the fact it installed means the
  Apple TV is on tvOS 27.0 or later. Record the exact version anyway.

### The build's own logging (this is the key signal)

The injected library logs through **`os_log`**:

- subsystem: **`tvport`**
- categories: `shim`, `cursor`, `input`, `lifecycle`, `storage`, `crash`
- mirrored to a rotated file: `Library/Caches/tvport/tvport.log` (plus
  `.log.1`, `.log.2`, `.log.3`) inside the app container
- log level from the `Info.plist` key `TVPortLogLevel` (shipped as `verbose`),
  overridable by the env var `TVPORT_LOG_LEVEL`

On a successful start it prints a **startup banner** containing a build id, the
Roblox version, the tvOS version, the device model, and a list of loaded shim
libraries. There are 19 of our dylibs in `Frameworks/`: `libtvport.dylib` plus
18 named `libtvportshim-*.dylib`.

**Whether that banner appears at all is the single most important fact you can
establish.** If it appears, dynamic linking succeeded and all the shims loaded.
If it never appears, the process died before our code ran, and the reason will
be in the system log, not in ours.

---

# 1. Hard rules

1. **Install nothing, and change no system state, until after STOP 1.** Phase 2
   is read-only inventory. I want to know what I already have before anything is
   added.
2. Use `sudo` only where a step genuinely needs it (network tunnel, pairing
   records). Say so before you do.
3. **Do not trust any command spelling I give you.** `pymobiledevice3`
   subcommands have moved between releases. Run `--help` on the version
   actually installed and use what it says. If a command I suggest does not
   exist, find the current equivalent and note the difference in your report.
4. Never disable TLS verification or security checks to work around an error.
5. If a step fails, capture the full error text verbatim into the evidence
   bundle and continue with the other steps. A failure is data.
6. Do not modify, re-sign or repackage the app. You are observing it.
7. Keep every raw artifact. Do not summarise away the original text.

Create a working directory first and keep everything under it:

```bash
mkdir -p ~/rbxtv-debug/{env,device,logs,crash,container,report} && cd ~/rbxtv-debug
```

---

# 2. Phase 0 — full inventory of this laptop (read-only)

I explicitly want a **whole-disk** check before anything is installed.

Record everything into `env/`. Cover at least:

**Python and the tools**

- every `python3*` on the system, with versions
- `pip`, `pipx`, `uv` presence and versions
- **`pymobiledevice3`**: is it installed, where, what version, in which
  environment (system, `--user`, pipx, a venv, conda)? Check
  `pip show pymobiledevice3` in each Python you find, and `pipx list`.
- `lldb` and `debugserver`: versions and paths. Debian usually needs
  `lldb` from the `lldb` package; note the LLVM version.
- `libimobiledevice` family: `idevice_id`, `ideviceinfo`,
  `ideviceinstaller`, `idevicesyslog`, `usbmuxd` — versions and paths
- `ios-deploy`, `ipsw`, `tidevice`, `go-ios`, `AltServer` — anything related
- `openssl`, `git`, `zip`, `tar`, `jq`

**Whole-disk search.** Use the fast path if available, otherwise `find`:

```bash
command -v plocate locate mlocate 2>/dev/null
```

```bash
sudo updatedb 2>/dev/null; plocate -i pymobiledevice3 2>/dev/null | head -50
```

If no locate database exists, fall back to a pruned find (do not let it grind
on `/proc`, `/sys`, `/dev`, network mounts or snapshots):

```bash
sudo find / -xdev \( -path /proc -o -path /sys -o -path /dev -o -path /run \) -prune -o \
  \( -iname 'pymobiledevice3*' -o -iname 'debugserver*' -o -iname '*.mobiledevicepairing' \) -print 2>/dev/null | head -100
```

Also search for Python virtual environments anywhere on disk and check each
one's installed packages:

```bash
sudo find / -xdev \( -path /proc -o -path /sys -o -path /dev \) -prune -o -name pyvenv.cfg -print 2>/dev/null | head -50
```

**Existing device state** — this matters, because a stale pairing record causes
confusing failures:

- `/var/lib/lockdown/` and `/var/db/lockdown/` (pairing records)
- `~/.pymobiledevice3/`
- any `~/.config/pymobiledevice3*`
- whether `usbmuxd` is running (`systemctl status usbmuxd`)

**Network**

- this laptop's interfaces and IPv4/IPv6 addresses
- whether mDNS works (`avahi-daemon` status; `avahi-browse -at` if present)
- whether the Apple TV is visible at all on the network

Write a table of findings to `env/INVENTORY.md`: tool, found?, version, path,
and how it is installed.

### ⛔ STOP 1

Report the inventory to me and tell me plainly:

- which required tools are **present and new enough**,
- which are **missing or too old**, and the exact commands you propose to fix
  that (prefer a venv or pipx over touching system Python; `pymobiledevice3`
  moves fast, so a recent version matters),
- whether any **stale pairing record** should be removed,
- anything surprising you found.

**Wait for my go-ahead before installing anything.**

---

# 3. Phase 1 — find and pair the Apple TV

After I approve:

1. Confirm the Apple TV is reachable. Find its name/address over mDNS and plain
   ping. Record into `device/`.
2. On the Apple TV, make sure these are on (tell me the menu path and let me do
   it): **Settings → General → Privacy → Share Analytics** is *not* what you
   want; what you need is pairing trust and, if present, developer mode.
   Note: tvOS has no separate "Developer Mode" toggle like iOS 16+ in all
   versions — establish what this tvOS version actually requires and tell me.
3. Pair over the network. Explore the installed version's actual syntax; on
   recent releases this is roughly:

```bash
pymobiledevice3 lockdown pair --network
```

   I will confirm the prompt on the TV. Capture the result.

4. Record the device's identity and OS version:

```bash
pymobiledevice3 lockdown info > device/lockdown_info.txt
```

5. tvOS 17+ puts the developer services behind a **RemoteXPC tunnel**. Start it
   and leave it running in its own terminal (it needs root):

```bash
sudo pymobiledevice3 remote tunneld
```

   or, depending on version, `sudo pymobiledevice3 lockdown start-tunnel`.
   Record the RSD address and port it prints into `device/tunnel.txt`; later
   commands need them.

**Known risk:** Wi-Fi-only tunnelling to tvOS has been unreliable on some
versions. If the tunnel will not come up, say so clearly, record the error, and
continue with Phase 2 and Phase 3 — plain `syslog` and crash reports often do
not need the tunnel, and they carry most of the answer.

---

# 4. Phase 2 — identify what is actually installed

1. List installed apps and find ours. It may be under a rewritten bundle id, so
   match on the name or on anything containing `roblox`:

```bash
pymobiledevice3 apps list > device/apps_list.json
```

Record the **exact bundle id**, the signer/team id, the version, and the
container paths. Everything later uses that id — call it `$BUNDLE_ID`.

2. **Check whether the installed binary has `get-task-allow`.** This decides
   whether a debugger can attach at all. atvloadly signs with its own generated
   entitlements and I had no way to supply mine, so this may be absent. Get the
   entitlements however the installed tooling allows and record them in
   `device/entitlements_installed.txt`. If `get-task-allow` is missing, say so
   prominently — it means no `debugserver` attach, and we rely on syslog and
   crash reports instead. That is not fatal to the investigation.

3. If you can reach the app container, list it and note whether
   `Library/Caches/tvport/` exists. Its presence alone proves our injected
   library ran at least as far as its initialiser.

---

# 5. Phase 3 — capture a launch attempt (the core of the job)

Order matters. **Start capturing before the app is launched.**

1. Start an **unfiltered** system log capture to file, in its own terminal:

```bash
pymobiledevice3 syslog live > logs/syslog_full.txt 2>&1
```

   Unfiltered is deliberate. If our `tvport` banner never appears, the
   explanation will be a **dyld** or **code signature** message from the system,
   which a `--match tvport` filter would hide.

2. In a second terminal, also capture just ours, for readability:

```bash
pymobiledevice3 syslog live --match tvport > logs/syslog_tvport.txt 2>&1
```

3. Now launch the app **twice**, and timestamp each attempt in your notes:

   - **(a) From the Home screen.** Tell me when to do it; I will use the Siri
     Remote. This is the real-world path.
   - **(b) Programmatically**, which usually produces a clearer error:

```bash
pymobiledevice3 developer dvt launch $BUNDLE_ID
```

   Capture the command's own stdout/stderr to `logs/launch_dvt.txt`. If it
   fails, the error text is itself a major clue — keep it verbatim.

4. Let each attempt run for ~60 seconds even if nothing visible happens, then
   stop the captures.

5. Pull crash reports:

```bash
pymobiledevice3 crash pull crash/ --erase false
```

   If `--erase` is not a valid flag on this version, just pull. List what came
   back. Our app's reports will be named after the bundle id or `Roblox`.

6. Try to pull our mirrored log file from the app container:

```bash
pymobiledevice3 afc pull Library/Caches/tvport/tvport.log container/tvport.log
```

   If `afc` cannot see the app container (it often only exposes media), try the
   `crash`/`house arrest`/`apps` file-access routes the installed version
   offers. If none work, note it — the syslog capture is the primary record and
   the file mirror is only a convenience.

---

# 6. Phase 4 — triage: what the log means

Work through this and write your conclusion into `report/FINDINGS.md`. Quote the
exact log lines you base each conclusion on.

**A. Does the `tvport` startup banner appear?**

- **Yes** → dynamic linking succeeded and all shims loaded. The port's linking
  work is validated. Now look for what happens *after*: Metal/shader errors,
  an uncaught exception, a watchdog kill. Go to C.
- **No** → the process died before our code ran. Go to B.

**B. No banner — find the system's reason.** Search `logs/syslog_full.txt` for:

- `Library not loaded`, `Symbol not found`, `no suitable image found`,
  `dyld`, `dlopen`, `Referenced from` → **a linking failure.** Capture the
  full dyld message; it names the exact library or symbol. This would
  contradict the build's own verification, so it is important and specific.
- `code signature`, `codesign`, `invalid signature`, `0xe8008001`,
  `0xe800801c`, `AMFI`, `amfid`, `Killed: 9`, `provisioning profile`,
  `entitlement` → **a signing/entitlement problem, not a port problem.** This
  is the most likely cause of "installed but won't open" with a free account
  and a tool that could not take custom entitlements. Note exactly which
  entitlement or check is named.
- `watchdog`, `failed to launch`, `exited abnormally`, `jetsam`,
  `EXC_CRASH`, `SIGKILL`, `SIGABRT`, `SIGSEGV`
- the bundle id, the word `Roblox`, and `libtvport`

**C. Banner present — look for the known risks, in this order.**

1. **Metal / shaders — the expected top risk.** The app ships 1,648
   iOS-compiled Metal shader libraries and it is unverified whether tvOS
   accepts them. Search for: `Metal`, `MTL`, `metallib`, `AIR`,
   `newLibraryWithData`, `shader`, `compiler`, `GPU`,
   `MTLCompilerService`, `Failed to create library`. If the app reaches a
   black screen or dies right after the banner, this is the prime suspect.
2. **Tamper detection.** The client probes for jailbreak artefacts by path.
   Search for `MobileSubstrate`, `libjailbreak`, `cydia`, `amfid_payload`,
   `jailbroken`, `tamper`, `integrity`. Do **not** attempt to defeat
   anything — just report whether it fired.
3. **Asset catalog.** The app's compiled asset catalog is tagged for iOS.
   Search for `CoreUI`, `Assets.car`, `CUICatalog`, `deploymentPlatform`,
   `asset`.
4. **The cursor's touch synthesiser.** The banner includes a `UITouch
   synthesis probe` line reporting which private ivars were found and whether
   it is `AVAILABLE` or `UNAVAILABLE`. Record it verbatim.
5. **The platform identity shim.** There is a line saying how many of 5
   overrides applied, with the real tvOS values beside the reported ones.
   Record it verbatim.
6. Every line containing `FIRST CALL` — each names a shim that was actually
   reached. Also the periodic `shim call counts` blocks.
7. Everything at `error` level, especially in the `crash` category.

**D. If there is a crash report**, symbolicate our frames. Unzip the dSYMs I
will give you (`dSYMs.zip`) and either use `lldb` with

```
settings append target.debug-file-search-paths <dir-with-dSYMs>
```

or `llvm-symbolizer` / `atos` equivalents available on Debian. Our frames are in
`libtvport.dylib` and `libtvportshim-*.dylib`. Roblox's own frames will not
symbolicate — that is expected, do not chase it.

---

# 7. Phase 5 — debugger attach (only if `get-task-allow` is present)

If and only if Phase 2 showed `get-task-allow`:

```bash
pymobiledevice3 developer debugserver start-server
```

Then in `lldb`, using the host and port it printed:

```
platform select remote-tvos
```

```
process connect connect://<host>:<port>
```

Get a backtrace of all threads at the failure point and save it to
`report/lldb_backtrace.txt`:

```
thread backtrace all
```

If attach fails, record the error and move on. Do not spend long on it; the
syslog and crash reports matter more.

---

# 8. Phase 6 — package the evidence for upload

1. Write `report/FINDINGS.md` containing:
   - the single-sentence answer: **why doesn't it start?**
   - which of A/B/C above applied, with the exact log lines quoted
   - the tvOS version, device model, installed bundle id, signer, and whether
     `get-task-allow` was present
   - the full startup banner verbatim, if it appeared
   - every `FIRST CALL` line, the shim call counts, the `UITouch synthesis
     probe` line and the `platform identity shim` line
   - all `error`-level lines
   - the exact versions of every tool you used, and any command whose spelling
     differed from what this prompt suggested
   - anything you could not determine, stated as unknown rather than guessed
2. Also write `report/TOOL_VERSIONS.txt`.
3. Bundle it:

```bash
cd ~ && tar czf rbxtv-debug.tar.gz rbxtv-debug && ls -l rbxtv-debug.tar.gz && sha256sum rbxtv-debug.tar.gz
```

4. **If the bundle is larger than about 20 MB, split it**, because I upload it
   through a chat that takes files in parts:

```bash
split -b 20M -d -a 3 rbxtv-debug.tar.gz rbxtv-debug.tar.gz.part && sha256sum rbxtv-debug.tar.gz rbxtv-debug.tar.gz.part*
```

   Trim the raw syslog first if it is huge: keep the full window around each
   launch attempt rather than hours of unrelated system chatter, and say in
   `FINDINGS.md` what you trimmed.

### ⛔ STOP 2

Show me `report/FINDINGS.md` and the list of files in the bundle before I
upload. Tell me if anything in there looks like it should not be shared (it
should be system logs and crash traces only, but check for anything personal —
my Apple ID, the team id, network names, tokens).

---

# 9. Reference — expected shim libraries

If the banner lists loaded shim libraries, these are the 19 to expect:

```
libtvport.dylib
libtvportshim-CoreFoundation.dylib
libtvportshim-CoreTelephony.dylib
libtvportshim-EventKitUI.dylib
libtvportshim-FileProvider.dylib
libtvportshim-MessageUI.dylib
libtvportshim-PDFKit.dylib
libtvportshim-PushKit.dylib
libtvportshim-QuickLook.dylib
libtvportshim-SafariServices.dylib
libtvportshim-UIKit.dylib
libtvportshim-WidgetKit.dylib
libtvportshim-iAd.dylib
libtvportshim-libswiftCallKit.dylib
libtvportshim-libswiftCoreMIDI.dylib
libtvportshim-libswiftDarwin.dylib
libtvportshim-libswiftDataDetection.dylib
libtvportshim-libswiftFileProvider.dylib
libtvportshim-libswiftPassKit.dylib
```

A missing one in a dyld error message points straight at the cause.

---

# 10. What I care about most

In priority order:

1. **Does the `tvport` banner appear?** Yes/no answers the biggest open
   question in the whole project.
2. If no — **the verbatim dyld or code-signature message** that explains why.
3. If yes — **whether Metal accepted the iOS-compiled shaders.**
4. Whether `get-task-allow` survived atvloadly's signing.

Be precise about what you observed versus what you inferred. "I could not
determine X" is a useful answer; a confident guess is not.
