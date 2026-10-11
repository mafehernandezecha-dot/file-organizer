// Reads image paths (one per line) on stdin and prints one JSON line per image:
// size, transparency, a palette-size estimate, Apple Vision scene labels,
// recognized text, and the number of faces (faces are counted, never identified).
// Everything runs on this Mac with Apple's built-in Vision framework.

import CoreGraphics
import Foundation
import ImageIO
import Vision

struct Analysis: Codable {
    var path: String
    var w: Int = 0
    var h: Int = 0
    var alpha: Bool = false
    var colors: Int = 0
    var labels: [[String]] = []
    var text: String = ""
    var faces: Int = 0
    var error: String? = nil
}

/// Distinct colors in a 64×64 nearest-neighbour sample, 5 bits per channel.
/// Digital mosaic designs use a small brick palette; photos use thousands.
func paletteColors(_ image: CGImage) -> Int {
    let side = 64
    guard let ctx = CGContext(
        data: nil, width: side, height: side, bitsPerComponent: 8, bytesPerRow: side * 4,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else { return -1 }
    ctx.interpolationQuality = .none
    ctx.draw(image, in: CGRect(x: 0, y: 0, width: side, height: side))
    guard let raw = ctx.data else { return -1 }
    let px = raw.bindMemory(to: UInt8.self, capacity: side * side * 4)
    var seen = Set<UInt32>()
    for i in stride(from: 0, to: side * side * 4, by: 4) {
        if px[i + 3] < 128 { continue }  // ignore transparent pixels
        let r = UInt32(px[i] >> 3), g = UInt32(px[i + 1] >> 3), b = UInt32(px[i + 2] >> 3)
        seen.insert((r << 10) | (g << 5) | b)
    }
    return seen.count
}

func analyze(_ path: String) -> Analysis {
    var result = Analysis(path: path)
    autoreleasepool {
        let url = URL(fileURLWithPath: path)
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else {
            result.error = "unreadable"
            return
        }
        if let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [String: Any] {
            result.w = (props[kCGImagePropertyPixelWidth as String] as? Int) ?? 0
            result.h = (props[kCGImagePropertyPixelHeight as String] as? Int) ?? 0
            result.alpha = (props[kCGImagePropertyHasAlpha as String] as? Bool) ?? false
        }
        let thumbOptions: [String: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways as String: true,
            kCGImageSourceCreateThumbnailWithTransform as String: true,
            kCGImageSourceThumbnailMaxPixelSize as String: 1024,
        ]
        guard let image = CGImageSourceCreateThumbnailAtIndex(
            source, 0, thumbOptions as CFDictionary) else {
            result.error = "no image data"
            return
        }
        result.colors = paletteColors(image)

        let classify = VNClassifyImageRequest()
        let text = VNRecognizeTextRequest()
        text.recognitionLevel = .fast
        text.usesLanguageCorrection = false
        let faces = VNDetectFaceRectanglesRequest()
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        do {
            try handler.perform([classify, text, faces])
        } catch {
            result.error = "vision: \(error.localizedDescription)"
            return
        }
        if let observations = classify.results as? [VNClassificationObservation] {
            result.labels = observations
                .filter { $0.confidence >= 0.1 }
                .prefix(12)
                .map { [$0.identifier, String(format: "%.2f", $0.confidence)] }
        }
        if let observations = text.results as? [VNRecognizedTextObservation] {
            let lines = observations.compactMap { $0.topCandidates(1).first?.string }
            result.text = String(lines.joined(separator: " ").prefix(500))
        }
        if let observations = faces.results as? [VNFaceObservation] {
            result.faces = observations.count
        }
    }
    return result
}

let encoder = JSONEncoder()
while let line = readLine(strippingNewline: true) {
    if line.isEmpty { continue }
    let result = analyze(line)
    if let data = try? encoder.encode(result), let json = String(data: data, encoding: .utf8) {
        print(json)
        fflush(stdout)
    }
}
