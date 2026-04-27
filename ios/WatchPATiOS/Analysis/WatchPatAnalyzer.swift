import Foundation

struct AnalysisEnvelope {
    let summaryText: String
    let summary: AnalysisSummaryFields
}

struct AnalysisSummaryFields: Codable {
    let recordingPath: String
    let packetCount: Int
    let durationMinutes: Double
    let ahi: Double?
    let pahi: Double?
    let prdi: Double?
    let apneaEvents: Int
    let centralEvents: Int
    let patApneaEvents: Int
    let patHypopneaEvents: Int
    let reraEvents: Int
    let meanHrBpm: Double?
    let maxHrBpm: Double?
    let meanSpo2: Double?
    let minSpo2: Double?
    let bodyPositions: [String: Int]

    private enum CodingKeys: String, CodingKey {
        case recordingPath = "recording_path"
        case packetCount = "packet_count"
        case durationMinutes = "duration_minutes"
        case ahi
        case pahi
        case prdi
        case apneaEvents = "apnea_events"
        case centralEvents = "central_events"
        case patApneaEvents = "pat_apnea_events"
        case patHypopneaEvents = "pat_hypopnea_events"
        case reraEvents = "rera_events"
        case meanHrBpm = "mean_hr_bpm"
        case maxHrBpm = "max_hr_bpm"
        case meanSpo2 = "mean_spo2"
        case minSpo2 = "min_spo2"
        case bodyPositions = "body_positions"
    }
}

enum WatchPatAnalyzerError: Error {
    case invalidLengthPrefix
}

final class WatchPatAnalyzer {
    private let parser: KaitaiBridge

    init(parser: KaitaiBridge = try! KaitaiBridge()) {
        self.parser = parser
    }

    func analyze(fileURL: URL) throws -> AnalysisEnvelope {
        let buffers = SensorBuffers()
        var positionCounts: [String: Int] = [:]
        let fileHandle = try FileHandle(forReadingFrom: fileURL)
        defer { try? fileHandle.close() }

        var packetIndex = 0
        while true {
            let header = try fileHandle.read(upToCount: 4) ?? Data()
            if header.isEmpty { break }
            guard header.count == 4 else { throw WatchPatAnalyzerError.invalidLengthPrefix }
            let length = Int(header.readUInt32LE(at: 0))
            let packetData = try fileHandle.read(upToCount: length) ?? Data()
            guard packetData.count == length else { break }

            let packet = try parser.parsePacket(packetData)
            if packet.header.opcode != WatchPatProtocol.respDataPacket {
                continue
            }

            let now = Double(packetIndex + 1)
            for record in packet.records {
                let kind = (record.recordID << 8) | record.recordType
                let payload = Data(base64Encoded: record.rawPayloadB64) ?? Data()
                switch kind {
                case 0x0111:
                    buffers.feedWaveform(channel: "OxiA", samples: Self.decodeByteDelta(payload))
                case 0x0211:
                    buffers.feedWaveform(channel: "OxiB", samples: Self.decodeByteDelta(payload))
                case 0x0311:
                    buffers.feedWaveform(channel: "PAT", samples: Self.decodeByteDelta(payload))
                case 0x0401:
                    buffers.feedWaveform(channel: "Chest", samples: Self.decodeNibbleDelta(payload))
                case 0x0510:
                    buffers.metricValue = record.metricValue ?? 0
                case 0x0600:
                    let subframes = record.motionSubframes ?? []
                    buffers.feedMotion(subframes)
                    let label = Self.positionLabel(for: subframes.last)
                    if let label {
                        positionCounts[label, default: 0] += 1
                    }
                case 0x0C00:
                    buffers.feedEvent("EVENT_0C")
                case 0x0D00:
                    buffers.feedEvent("EVENT_0D")
                default:
                    break
                }
            }

            buffers.packetCount += 1
            buffers.totalBytes += packetData.count
            buffers.updateDerived(now: now)
            packetIndex += 1
        }

        let patApnea = buffers.patEvents.filter { $0.type == "APNEA" }.count
        let patHypopnea = buffers.patEvents.filter { $0.type == "HYPOPNEA" }.count
        let rera = buffers.patEvents.filter { $0.type == "RERA" }.count
        let durationMinutes = Double(buffers.packetCount) / 60.0

        let summary = AnalysisSummaryFields(
            recordingPath: fileURL.path,
            packetCount: buffers.packetCount,
            durationMinutes: Self.round(durationMinutes, digits: 2),
            ahi: Self.clean(buffers.ahiEstimate),
            pahi: Self.clean(buffers.pahiEstimate),
            prdi: Self.clean(buffers.rdiEstimate),
            apneaEvents: buffers.apneaEvents.count,
            centralEvents: buffers.centralEvents.count,
            patApneaEvents: patApnea,
            patHypopneaEvents: patHypopnea,
            reraEvents: rera,
            meanHrBpm: Self.clean(Self.meanPositive(buffers.hrHistory)),
            maxHrBpm: Self.clean(buffers.hrHistory.filter { $0 > 0 }.max()),
            meanSpo2: Self.clean(Self.meanPositive(buffers.spo2FullHistory)),
            minSpo2: Self.clean(buffers.spo2FullHistory.filter { $0 > 0 }.min()),
            bodyPositions: positionCounts
        )

        let text = [
            "=== Sleep Analysis ===",
            "Duration:   \(Int(durationMinutes.rounded())) min  (\(buffers.packetCount) packets)",
            "AHI:        \(Self.format(summary.ahi)) /hr",
            "pAHI:       \(Self.format(summary.pahi)) /hr",
            "pRDI:       \(Self.format(summary.prdi)) /hr",
            "Apneas:     \(summary.apneaEvents)  (\(summary.centralEvents) central)",
            "PAT events: \(summary.patApneaEvents) apnea, \(summary.patHypopneaEvents) hypopnea, \(summary.reraEvents) RERA",
            "Mean HR:    \(Self.format(summary.meanHrBpm)) bpm",
            "Max HR:     \(Self.format(summary.maxHrBpm)) bpm",
            "Mean SpO2:  \(Self.format(summary.meanSpo2))%",
            "Min SpO2:   \(Self.format(summary.minSpo2))%",
            "======================",
        ].joined(separator: "\n")

        return AnalysisEnvelope(summaryText: text, summary: summary)
    }

    private static func decodeByteDelta(_ payload: Data) -> [Double] {
        guard payload.count >= 2 else { return [] }
        let seed = Int16(littleEndian: payload.withUnsafeBytes { $0.load(as: Int16.self) })
        var acc = Int(seed)
        var samples = [Double(acc)]
        for byte in payload.dropFirst(2) {
            let delta = Int((byte >> 1)) ^ -Int(byte & 1)
            acc += delta
            samples.append(Double(acc))
        }
        return samples
    }

    private static func decodeNibbleDelta(_ payload: Data) -> [Double] {
        guard payload.count >= 3 else { return [] }
        let seed = Int16(littleEndian: payload.withUnsafeBytes { $0.load(as: Int16.self) })
        var acc = Int(seed)
        var samples = [Double(acc)]
        for byte in payload.dropFirst(3) {
            let lowNibble = Int(byte & 0x0F)
            let highNibble = Int((byte >> 4) & 0x0F)
            let delta = lowNibble >= 8 ? lowNibble - 16 : lowNibble
            acc += delta
            samples.append(Double(acc))
            if highNibble == 0 || highNibble == 7 {
                samples.append(Double(acc))
            }
        }
        return samples
    }

    private static func positionLabel(for subframe: ParsedWatchPatPacket.Record.MotionSubframe?) -> String? {
        guard let subframe else { return nil }
        let axes = [
            ("x+", subframe.x),
            ("x-", -subframe.x),
            ("y+", subframe.y),
            ("y-", -subframe.y),
            ("z+", subframe.z),
            ("z-", -subframe.z),
        ]
        guard let best = axes.max(by: { $0.1 < $1.1 }) else { return nil }
        switch best.0 {
        case "z+": return "Supine"
        case "z-": return "Prone"
        case "y+": return "Left"
        case "y-": return "Right"
        case "x+": return "Upright"
        case "x-": return "Inverted"
        default: return nil
        }
    }

    private static func meanPositive(_ values: [Double]) -> Double? {
        let valid = values.filter { !$0.isNaN && $0 > 0 }
        guard !valid.isEmpty else { return nil }
        return valid.reduce(0, +) / Double(valid.count)
    }

    private static func clean(_ value: Double?) -> Double? {
        guard let value, !value.isNaN, value >= 0 else { return nil }
        return Foundation.round(value * 100) / 100
    }

    private static func round(_ value: Double, digits: Int) -> Double {
        let scale = pow(10.0, Double(digits))
        return Foundation.round(value * scale) / scale
    }

    private static func format(_ value: Double?) -> String {
        guard let value else { return "n/a" }
        return String(format: "%.1f", value)
    }
}

private final class SensorBuffers {
    private let waveformRate = 100

    var oxiA: [Double] = []
    var oxiB: [Double] = []
    var pat: [Double] = []
    var chest: [Double] = []
    var accelX: [Double] = []
    var accelY: [Double] = []
    var accelZ: [Double] = []
    var hrHistory: [Double] = []
    var spo2History: [Double] = []
    var spo2FullHistory: [Double] = []
    var patEvents: [(start: Double, ratio: Double, hrRise: Double, spo2Drop: Double, type: String)] = []
    var apneaEvents: [(start: Double, nadir: Double)] = []
    var centralEvents: [(start: Double, drop: Double)] = []
    var packetCount = 0
    var totalBytes = 0
    var metricValue = 0
    var startTime = 0.0
    var ahiEstimate = -1.0
    var pahiEstimate = -1.0
    var rdiEstimate = -1.0

    private var currentHR = -1.0
    private var currentSpO2 = -1.0
    private var spo2ScoreAB = 0.0
    private var spo2ScoreBA = 0.0
    private var spo2Raw: [Double] = []
    private var spo2EMA = -1.0
    private var desatBaseline: [Double] = []
    private var desatInEvent = false
    private var desatStart = 0.0
    private var desatNadir = 100.0
    private var patBaseline: [Double] = []
    private var patInEvent = false
    private var patEventStart = 0.0
    private var patHRBaseline = -1.0
    private var eventLog: [String] = []

    func feedWaveform(channel: String, samples: [Double]) {
        switch channel {
        case "OxiA":
            extend(&oxiA, with: samples, max: 1000)
        case "OxiB":
            extend(&oxiB, with: samples, max: 1000)
        case "PAT":
            extend(&pat, with: samples, max: 1000)
        case "Chest":
            extend(&chest, with: samples, max: 1000)
        default:
            break
        }
    }

    func feedMotion(_ subframes: [ParsedWatchPatPacket.Record.MotionSubframe]) {
        for subframe in subframes {
            appendTrimmed(&accelX, Double(subframe.x), max: 50)
            appendTrimmed(&accelY, Double(subframe.y), max: 50)
            appendTrimmed(&accelZ, Double(subframe.z), max: 50)
        }
    }

    func feedEvent(_ event: String) {
        eventLog.append(event)
        if eventLog.count > 20 {
            eventLog.removeFirst(eventLog.count - 20)
        }
    }

    func updateDerived(now: Double) {
        if startTime == 0 {
            startTime = now
        }

        currentHR = computeHeartRate()
        appendTrimmed(&hrHistory, currentHR > 0 ? currentHR : .nan, max: 120)

        let spo2 = computeSpO2(hr: currentHR)
        appendTrimmed(&spo2Raw, spo2, max: 15)
        let validRaw = spo2Raw.filter { $0 > 0 }.sorted()
        if !validRaw.isEmpty {
            let trim = max(1, validRaw.count / 5)
            let trimmed = validRaw.count > 4 ? Array(validRaw[trim..<(validRaw.count - trim)]) : validRaw
            let median = trimmed[trimmed.count / 2]
            if spo2EMA < 0 {
                spo2EMA = median
            } else {
                spo2EMA += 0.15 * (median - spo2EMA)
            }
            currentSpO2 = spo2EMA
            appendTrimmed(&spo2History, spo2EMA, max: 120)
        } else {
            currentSpO2 = -1
            appendTrimmed(&spo2History, .nan, max: 120)
        }

        let elapsed = now - startTime
        if currentSpO2 > 0 {
            appendTrimmed(&spo2FullHistory, currentSpO2, max: 14_400)
            if !desatInEvent {
                appendTrimmed(&desatBaseline, currentSpO2, max: 120)
            }
            if desatBaseline.count >= 30 {
                let baseline = percentile(desatBaseline, percentile: 0.9)
                let drop = baseline - currentSpO2
                if !desatInEvent {
                    if drop >= 3.0 {
                        desatInEvent = true
                        desatStart = elapsed
                        desatNadir = currentSpO2
                    }
                } else {
                    desatNadir = min(desatNadir, currentSpO2)
                    if drop < 1.0 {
                        if elapsed - desatStart >= 10.0 {
                            apneaEvents.append((desatStart, desatNadir))
                            let concurrentPAT = patInEvent || patEvents.contains { abs($0.start - desatStart) <= 60.0 }
                            if !concurrentPAT {
                                centralEvents.append((desatStart, baseline - desatNadir))
                            }
                        }
                        desatInEvent = false
                    }
                }
            }
        }

        if pat.count >= waveformRate * 3 {
            let tail = Array(pat.suffix(waveformRate * 3))
            let env = (tail.max() ?? 0) - (tail.min() ?? 0)
            if env > 0 {
                if !patInEvent {
                    appendTrimmed(&patBaseline, env, max: 300)
                }
                if patBaseline.count >= 30 {
                    let baseline = percentile(patBaseline, percentile: 0.75)
                    let ratio = baseline > 0 ? env / baseline : 1.0
                    if !patInEvent {
                        if ratio <= 0.70 {
                            patInEvent = true
                            patEventStart = elapsed
                            patHRBaseline = currentHR
                        }
                    } else {
                        let recovered = ratio > 0.80
                        let timedOut = elapsed - patEventStart > 120.0
                        if recovered || timedOut {
                            let duration = elapsed - patEventStart
                            if duration >= 10.0 && duration <= 120.0 {
                                let hrRise = (patHRBaseline > 0 && currentHR > 0) ? currentHR - patHRBaseline : 0.0
                                let lookback = min(Int(duration) + 5, spo2FullHistory.count)
                                let recent = lookback > 0 ? Array(spo2FullHistory.suffix(lookback)) : []
                                let valid = recent.filter { $0 > 0 }
                                let spo2Drop = valid.isEmpty ? 0.0 : max(0.0, (valid.max() ?? 0) - (valid.min() ?? 0))
                                let eventType: String
                                if spo2Drop >= 4.0 {
                                    eventType = "APNEA"
                                } else if spo2Drop >= 3.0 {
                                    eventType = "HYPOPNEA"
                                } else if hrRise >= 6.0 {
                                    eventType = "RERA"
                                } else {
                                    eventType = "PAT"
                                }
                                patEvents.append((patEventStart, ratio, hrRise, spo2Drop, eventType))
                            }
                            patInEvent = false
                        }
                    }
                }
            }
        }

        if elapsed >= 60 {
            let hours = elapsed / 3600.0
            let apneas = patEvents.filter { $0.type == "APNEA" }.count
            let hypopneas = patEvents.filter { $0.type == "HYPOPNEA" }.count
            let reras = patEvents.filter { $0.type == "RERA" }.count
            ahiEstimate = Double(apneaEvents.count) / hours
            pahiEstimate = Double(apneas + hypopneas) / hours
            rdiEstimate = Double(apneas + hypopneas + reras) / hours
        }
    }

    private func computeHeartRate() -> Double {
        for buffer in [pat, oxiB, oxiA] where buffer.count >= waveformRate * 5 {
            let rate = detectHeartRate(samples: buffer)
            if rate > 0 {
                return rate
            }
        }
        return -1
    }

    private func computeSpO2(hr: Double) -> Double {
        let minSamples = waveformRate * 4
        guard hr > 0, oxiA.count >= minSamples, oxiB.count >= minSamples else {
            return -1
        }
        let tailCount = min(oxiA.count, oxiB.count, waveformRate * 4)
        let aTail = Array(oxiA.suffix(tailCount))
        let bTail = Array(oxiB.suffix(tailCount))
        let dcA = average(aTail)
        let dcB = average(bTail)
        let dcRatio = (abs(dcA) > 10 && abs(dcB) > 10) ? (dcA / dcB) : 1.0
        guard abs(dcRatio - 1.0) > 0.08 else { return -1 }

        let ab = computeSpO2Pair(red: oxiA, ir: oxiB, bpm: hr)
        let ba = computeSpO2Pair(red: oxiB, ir: oxiA, bpm: hr)
        spo2ScoreAB *= 0.9
        spo2ScoreBA *= 0.9
        if ab.spo2 > 0, ab.ratio > 0, ab.ratio >= 0.4, ab.ratio <= 1.3 {
            spo2ScoreAB += 1.0 - abs(ab.ratio - 0.7)
        }
        if ba.spo2 > 0, ba.ratio > 0, ba.ratio >= 0.4, ba.ratio <= 1.3 {
            spo2ScoreBA += 1.0 - abs(ba.ratio - 0.7)
        }
        return spo2ScoreAB >= spo2ScoreBA ? (ab.spo2 > 0 ? ab.spo2 : ba.spo2) : (ba.spo2 > 0 ? ba.spo2 : ab.spo2)
    }

    private func detectHeartRate(samples: [Double]) -> Double {
        let peaks = detectPeaks(samples: samples)
        guard peaks.count >= 3 else { return -1 }
        let minInterval = Double(waveformRate) * 60.0 / 140.0
        let maxInterval = Double(waveformRate) * 60.0 / 40.0
        var bpms: [Double] = []
        for pair in zip(peaks, peaks.dropFirst()) {
            let delta = Double(pair.1 - pair.0)
            if delta >= minInterval && delta <= maxInterval {
                bpms.append(60.0 * Double(waveformRate) / delta)
            }
        }
        guard bpms.count >= 2 else { return -1 }
        return median(bpms)
    }

    private func detectPeaks(samples: [Double]) -> [Int] {
        guard samples.count >= waveformRate * 3 else { return [] }
        let window = max(3, Int(Double(waveformRate) * 0.75))
        let baseline = movingAverage(samples: samples, window: window)
        let detrended = zip(samples, baseline).map(-)
        let absMedian = median(detrended.map(abs))
        let threshold = max(20.0, absMedian * 1.5)
        let refractory = max(1, Int(Double(waveformRate) * 0.35))
        let prominenceWindow = max(3, Int(Double(waveformRate) * 0.15))
        var peaks: [Int] = []
        var lastPeak = -refractory
        for index in 1..<(detrended.count - 1) {
            if index - lastPeak < refractory { continue }
            let current = detrended[index]
            if current <= threshold || current <= detrended[index - 1] || current < detrended[index + 1] {
                continue
            }
            let lo = max(0, index - prominenceWindow)
            let hi = min(detrended.count, index + prominenceWindow + 1)
            let localMin = detrended[lo..<hi].min() ?? current
            if current - localMin < threshold * 1.2 {
                continue
            }
            peaks.append(index)
            lastPeak = index
        }
        return peaks
    }

    private func computeSpO2Pair(red: [Double], ir: [Double], bpm: Double) -> (spo2: Double, ratio: Double) {
        let count = min(red.count, ir.count, waveformRate * 4)
        guard count >= waveformRate * 2 else { return (-1, -1) }
        let redWindow = Array(red.suffix(count))
        let irWindow = Array(ir.suffix(count))
        let redDC = average(redWindow)
        let irDC = average(irWindow)
        guard abs(redDC) >= 10, abs(irDC) >= 10 else { return (-1, -1) }
        let pulseHz = bpm / 60.0
        let redAC = sinusoidAmplitude(samples: redWindow, hz: pulseHz)
        let irAC = sinusoidAmplitude(samples: irWindow, hz: pulseHz)
        guard redAC > 0, irAC > 0 else { return (-1, -1) }
        if redAC / abs(redDC) > 0.5 || irAC / abs(irDC) > 0.5 {
            return (-1, -1)
        }
        let ratio = abs((redAC / redDC) / (irAC / irDC))
        let spo2 = 116.0 - 25.0 * ratio
        return (spo2 >= 60 && spo2 <= 100) ? (spo2, ratio) : (-1, ratio)
    }

    private func sinusoidAmplitude(samples: [Double], hz: Double) -> Double {
        guard !samples.isEmpty, hz > 0 else { return 0 }
        let meanValue = average(samples)
        var realPart = 0.0
        var imagPart = 0.0
        for (index, sample) in samples.enumerated() {
            let angle = 2.0 * .pi * hz * Double(index) / Double(waveformRate)
            let centered = sample - meanValue
            realPart += centered * cos(angle)
            imagPart -= centered * sin(angle)
        }
        return (2.0 / Double(samples.count)) * sqrt(realPart * realPart + imagPart * imagPart)
    }

    private func movingAverage(samples: [Double], window: Int) -> [Double] {
        guard !samples.isEmpty else { return [] }
        var result = Array(repeating: 0.0, count: samples.count)
        var running = 0.0
        for index in samples.indices {
            running += samples[index]
            if index >= window {
                running -= samples[index - window]
            }
            let count = min(index + 1, window)
            result[index] = running / Double(count)
        }
        return result
    }

    private func percentile(_ values: [Double], percentile: Double) -> Double {
        let sorted = values.sorted()
        let index = Int(Double(sorted.count - 1) * percentile)
        return sorted[max(0, min(sorted.count - 1, index))]
    }

    private func median(_ values: [Double]) -> Double {
        let sorted = values.sorted()
        guard !sorted.isEmpty else { return -1 }
        if sorted.count % 2 == 0 {
            return (sorted[sorted.count / 2] + sorted[(sorted.count / 2) - 1]) / 2.0
        }
        return sorted[sorted.count / 2]
    }

    private func average(_ values: [Double]) -> Double {
        guard !values.isEmpty else { return 0 }
        return values.reduce(0, +) / Double(values.count)
    }

    private func appendTrimmed(_ values: inout [Double], _ value: Double, max: Int) {
        values.append(value)
        if values.count > max {
            values.removeFirst(values.count - max)
        }
    }

    private func extend(_ values: inout [Double], with samples: [Double], max: Int) {
        values.append(contentsOf: samples)
        if values.count > max {
            values.removeFirst(values.count - max)
        }
    }
}

private extension Data {
    func readUInt32LE(at offset: Int) -> UInt32 {
        UInt32(self[offset])
        | (UInt32(self[offset + 1]) << 8)
        | (UInt32(self[offset + 2]) << 16)
        | (UInt32(self[offset + 3]) << 24)
    }
}
