//
//  standin_PDFKit.m — stand-in for PDFKit, absent on tvOS
//
//  Evidence
//  --------
//  Missing on device (source 1): PDFKit.framework is not among the images in
//  the tvOS 27.2 (24K122) shared cache for AppleTV14,1.
//  Missing from the public API (source 2): absent from tvOS SDK 27.2.
//  Contract (source 3), iOS SDK 26.2,
//  System/Library/Frameworks/PDFKit.framework/Headers/PDFDocument.h:
//      @interface PDFDocument : NSObject <NSCopying>
//
//  Imported by (source 4): Persona2 imports _OBJC_CLASS_$_PDFDocument,
//  non-weak and eager. There is no metaclass import, so it is not subclassed.
//
//  Selectors Persona2 references (source 4): -initWithData: and -pageCount.
//  Nothing else from PDFKit appears in its __objc_selrefs, so nothing else is
//  implemented (CLAUDE.md Phase 4).
//
//  Decision: STUB returning an empty document.
//
//  Reason: Persona2 uses this to look at a PDF it has downloaded (a consent or
//  policy document in the identity flow). The flow itself cannot run on tvOS
//  anyway, because it needs the SFSafariViewController web view that is also
//  absent. -initWithData: therefore keeps the object alive and reports
//  pageCount 0, which is what PDFKit itself returns for data it cannot parse,
//  so the caller takes its existing "no pages" path rather than meeting a
//  missing class.
//
//  Status: UNTESTED (no device run yet).
//
#import <Foundation/Foundation.h>

#include "shimsupport.h"

TVPORT_SHIM_LIBRARY("standin_PDFKit (PDFDocument)")

@interface PDFDocument : NSObject <NSCopying>
- (instancetype)initWithData:(NSData *)data;
- (instancetype)initWithURL:(NSURL *)url;
@property (nonatomic, readonly) NSUInteger pageCount;
@end

@implementation PDFDocument {
    NSUInteger _byteCount;
}

- (instancetype)initWithData:(NSData *)data {
    TVPORT_SHIM_CALL("PDFDocument -initWithData:", "STUB -> empty document");
    self = [super init];
    if (self) {
        _byteCount = data.length;
        TVLOG_W(TVLogShim, "PDFDocument stand-in created from %lu bytes; PDFKit is "
                           "absent on tvOS so the document reports 0 pages",
                (unsigned long)_byteCount);
    }
    return self;
}

- (instancetype)initWithURL:(NSURL *)url {
    TVPORT_SHIM_CALL("PDFDocument -initWithURL:", "STUB -> empty document");
    self = [super init];
    return self;
}

- (NSUInteger)pageCount {
    TVPORT_SHIM_CALL("PDFDocument -pageCount", "STUB -> 0");
    return 0;
}

- (id)copyWithZone:(NSZone *)zone {
    TVPORT_SHIM_CALL("PDFDocument -copyWithZone:", "STUB");
    return [[[self class] alloc] initWithData:[NSData data]];
}

@end
