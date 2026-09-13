#!/usr/bin/env swift
// Lay N screenshots side by side, numbered, on the app's dark ground.
// usage: swift scripts/contact_sheet.swift out.png 1.png 2.png ...
import AppKit

let args = CommandLine.arguments
guard args.count >= 3 else {
    FileHandle.standardError.write("usage: contact_sheet.swift out.png in1.png in2.png ...\n".data(using: .utf8)!)
    exit(1)
}
let out = args[1]
let inputs = Array(args[2...])
let images = inputs.compactMap { NSImage(contentsOfFile: $0) }
guard images.count == inputs.count else {
    FileHandle.standardError.write("could not read one of the inputs\n".data(using: .utf8)!)
    exit(1)
}

let tileHeight: CGFloat = 720
let gap: CGFloat = 32
let labelBand: CGFloat = 56

let tiles: [NSSize] = images.map { img in
    let rep = img.representations[0]
    let w = CGFloat(rep.pixelsWide), h = CGFloat(rep.pixelsHigh)
    return NSSize(width: (w * tileHeight / h).rounded(), height: tileHeight)
}
let width = tiles.reduce(0) { $0 + $1.width } + gap * CGFloat(tiles.count + 1)
let height = tileHeight + labelBand + gap * 2

let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(width), pixelsHigh: Int(height),
                              bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                              colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
NSColor(calibratedRed: 0.075, green: 0.075, blue: 0.082, alpha: 1).setFill()
NSRect(x: 0, y: 0, width: width, height: height).fill()

let attrs: [NSAttributedString.Key: Any] = [
    .font: NSFont.monospacedSystemFont(ofSize: 30, weight: .bold),
    .foregroundColor: NSColor.white,
]
var x = gap
for (i, img) in images.enumerated() {
    let s = tiles[i]
    img.draw(in: NSRect(x: x, y: gap, width: s.width, height: s.height),
             from: .zero, operation: .sourceOver, fraction: 1)
    ("\(i + 1)" as NSString).draw(at: NSPoint(x: x, y: gap + tileHeight + 12), withAttributes: attrs)
    x += s.width + gap
}
NSGraphicsContext.restoreGraphicsState()

let data = bitmap.representation(using: .png, properties: [:])!
try! data.write(to: URL(fileURLWithPath: out))
print(out)
