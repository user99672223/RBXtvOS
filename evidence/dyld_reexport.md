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

* `dyld-1378` is the newest published source at the time of writing. The device
  runs tvOS 27.2 (24K122); the `dyld-version` string from the device's own cache
  is collected by the `dyld-version` query in
  `evidence/queries/device_queries.json` so the two can be compared. The
  re-export rule above is long-standing, but the exact shipped version is
  recorded rather than assumed. **Status: version match UNVERIFIED until that
  query's output lands.**
* Symbol-level re-export aliases (`EXPORT_SYMBOL_FLAGS_REEXPORT` inside the
  trie, handled at line 3269) are a separate mechanism and are not used here.
* This says nothing about *ObjC class* realization, only about symbol binding.
  A class our proxy defines is realized by the ObjC runtime from our dylib's
  `__objc_classlist` like any other; that is why the stand-ins declare the real
  superclass from the iOS SDK.
