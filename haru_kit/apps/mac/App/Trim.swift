// The silence trim of a voice reference, the same as upstream `prepare_reference`
// (haru_mlx/audio.py): the model gets this audio, so the preview plays it.

import Foundation

/// Mono float audio at 24 kHz: leading and trailing audio quieter than -45 dBFS
/// (RMS of 20 ms windows) removed, with 0.2 s kept around the speech; then at
/// Most the last `maxSeconds`.
func trimSilence(_ audio: [Float], rate: Int = 24_000, maxSeconds: Double = 180) -> [Float] {
    let window = rate / 50
    let count = audio.count / window
    var trimmed = audio
    if count > 0 {
        var first = -1, last = -1
        for w in 0..<count {
            var sum: Float = 0
            for i in w * window..<(w + 1) * window { sum += audio[i] * audio[i] }
            let rms = (sum / Float(window) + 1e-12).squareRoot()
            if 20 * log10(rms) > -45 {
                if first < 0 { first = w }
                last = w
            }
        }
        if first < 0 { return [] }
        let margin = Int(0.2 * Double(rate))
        let start = max(0, first * window - margin)
        let end = min(audio.count, (last + 1) * window + margin)
        trimmed = Array(audio[start..<end])
    }
    let keep = Int(maxSeconds * Double(rate))
    return trimmed.count > keep ? Array(trimmed.suffix(keep)) : trimmed
}
