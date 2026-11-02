# Does a proxy dylib's re-export satisfy a two-level-namespace bind?

Evidence source 5 (Apple's open-source dyld). This is the single assumption the
Phase 4 **proxy** shims rest on, so CLAUDE.md requires it to be checked against
dyld's own code rather than taken on trust.

## The question

Roblox imports, say, `_OBJC_CLASS_$_UIHoverGestureRecognizer` **from**
`/System/Library/Frameworks/UIKit.framework/UIKit`. That is a two-level-namespace
bind: the import names both the symbol and the library it must come from.

Phase 5 repoints that load command to `@rpath/libtvportshim-UIKit.dylib`, a
dylib that

* defines `UIHoverGestureRecognizer`, and
* carries `LC_REEXPORT_DYLIB` for the real UIKit.

For this to work, dyld must do two things: find the one symbol we define in our
own dylib, and find **all the other** UIKit symbols Roblox imports from that
same load command by following our re-export into the real UIKit. If it only
looked in our dylib, every other UIKit import would break.

## What dyld does

Source: `apple-oss-distributions/dyld`, tag **`dyld-1378`** (commit `fd8d0c4`),
`dyld/Loader.cpp`, `Loader::hasExportedSymbol` (declared at line 3196).

A two-level-namespace bind resolves in `staticLink` mode, which sets
(lines 3212-3217):

```cpp
        case staticLink:
            canSearchDependents = true;
            searchNonReExports  = false;
            searchSelf          = true;
            depsMode            = staticLink;
            break;
```

`searchSelf = true` means the named library's own exports trie is consulted, so
the symbol our proxy defines is found there.

`canSearchDependents = true` then reaches this, at the end of the same function
(lines 3438-3452):

```cpp
    if ( canSearchDependents ) {
        // Search re-exported dylibs
        const uint32_t depCount = this->dependentCount();
        for ( uint32_t i = 0; i < depCount; ++i ) {
            Loader::LinkedDylibAttributes depAttrs;
            if ( Loader* depLoader = this->dependent(state, i, &depAttrs) ) {
                // when dlsym() continues to search (searchNonReExports), don't follow upward or delay linkages
                if ( depAttrs.reExport || (searchNonReExports && !depAttrs.upward && !depAttrs.delayInit)  ) {
                    if ( depLoader->hasExportedSymbol(diag, state, symbolName, depsMode, resolverMode, result, alreadySearched) )
                        return true;
                }
            }
        }
    }
    return false;
```

Because `searchNonReExports` is **false** in `staticLink` mode, the condition
reduces to `depAttrs.reExport` alone: dyld recurses only into dependents linked
with `LC_REEXPORT_DYLIB`, and it does so with `depsMode = staticLink`, so the
recursion keeps the same rule all the way down.

## Conclusion

Both halves hold, and for the same reason:

1. A symbol the proxy defines itself is found via `searchSelf`.
2. A symbol the proxy does not define is found by recursing into its
   `LC_REEXPORT_DYLIB` dependents — the real framework.

So repointing a load command at a proxy that re-exports the original is
sufficient, and it is sufficient *by design* rather than by accident: this is
the mechanism umbrella frameworks already use. The device's own UIKit is itself
an example — its `LC_REEXPORT_DYLIB` list names UIKitCore, PrintKitUI and
ShareSheet, and 51,191 of the names reachable from "UIKit" on tvOS 27.2 arrive
exactly this way.

## Caveats recorded

* **The versions do not match, and that is now measured rather than open.** The
  device's own cache carries

  ```
  0x180518980: "@(#)PROGRAM:dyld  PROJECT:dyld-27104"   image=dyld
  ```

  so tvOS 27.2 (24K122) ships **`dyld-27104`**, while the newest *published*
  open-source drop is **`dyld-1378`** — the version numbering was rebased onto
  the OS train, so the gap is not 25,726 revisions, but it is a different build
  and Apple has published no source for it.

  What that does and does not undermine: the two-level-namespace rule quoted
  above is long-standing behaviour that umbrella frameworks have depended on
  for over a decade, and the device's own UIKit relies on it right now (its
  `LC_REEXPORT_DYLIB` list names UIKitCore, PrintKitUI and ShareSheet, and
  51,191 of the names reachable from "UIKit" on this device arrive that way).
  So the mechanism is certainly present on the device. What cannot be claimed
  is that the *code path* was read from the exact binary that will run.
  **Status: mechanism VERIFIED on the device by observation; source reading
  done against a different build (`dyld-1378` vs `dyld-27104`).**

* The `objc-version` query found only the bare string `objc4` in
  `libobjc.A.dylib` with no version suffix, so the objc4 revision this device
  ships is **UNVERIFIED**. Nothing here depends on it: the shims rely on
  ordinary class realization, not on any objc4 internal.
* Symbol-level re-export aliases (`EXPORT_SYMBOL_FLAGS_REEXPORT` inside the
  trie, handled at line 3269) are a separate mechanism and are not used here.
* This says nothing about *ObjC class* realization, only about symbol binding.
  A class our proxy defines is realized by the ObjC runtime from our dylib's
  `__objc_classlist` like any other; that is why the stand-ins declare the real
  superclass from the iOS SDK.
