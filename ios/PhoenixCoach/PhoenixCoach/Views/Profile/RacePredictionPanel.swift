import SwiftUI

/// Profile's "Predicted <race>" panel: the target as a line, both predictors
/// as ticks on it — Phoenix (from the athlete's race efforts) filled, COROS
/// (the watch's fitness model) hollow. Chosen in the 2026-10-09 /variants
/// round (V4) over two stacked panels, which "looked off". Either predictor
/// may be missing; the bar still draws with one tick. The scale always
/// contains the target and every tick, so a predictor faster than the
/// target lands left of the line instead of off the panel.
struct RacePredictionPanel: View {
    let prediction: RacePrediction?
    let coros: CorosPrediction?
    let raceDistance: String?
    let targetFinishTime: String?

    private var targetSec: Int? { Self.parseHMS(targetFinishTime) }

    /// COROS's number for the goal distance, if it has one.
    private var corosSec: Int? {
        switch raceDistance {
        case "Half Marathon", "Half": return coros?.halfSec
        case "Marathon": return coros?.marathonSec
        default: return nil
        }
    }

    private var corosText: String? {
        switch raceDistance {
        case "Half Marathon", "Half": return coros?.half
        case "Marathon": return coros?.marathon
        default: return nil
        }
    }

    var body: some View {
        if prediction?.predictedSec != nil || corosSec != nil {
            VStack(alignment: .leading, spacing: DS.Spacing.m) {
                HStack {
                    label("Predicted \(prediction?.distance ?? raceDistance ?? "race")")
                    Spacer()
                    if let t = targetFinishTime {
                        label("target \(Self.trimLeadingZero(t))", color: .white)
                    }
                }
                bar
                legend
                Text(footnote)
                    .font(.system(size: 13, weight: .light))
                    .foregroundStyle(DS.Colors.onSurface)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(DS.Spacing.m)
            .background(Color.white.opacity(0.03))
            .clipShape(.rect(cornerRadius: DS.Radius.medium))
            .accessibilityElement(children: .combine)
            .accessibilityLabel(accessibilityText)
        }
    }

    // MARK: Pieces

    private func label(_ text: String, color: Color = DS.Colors.outline) -> some View {
        Text(text)
            .font(.system(size: 10, weight: .bold))
            .textCase(.uppercase)
            .tracking(DS.Tracking.wide)
            .foregroundStyle(color)
    }

    /// Window: from 2 minutes under the earliest point to 2 minutes past the
    /// latest, never narrower than 20 minutes, so a 10-minute spread reads as
    /// half the bar rather than a hairline.
    private var window: (lo: Double, hi: Double) {
        let points = [targetSec, prediction?.predictedSec, corosSec].compactMap { $0 }.map(Double.init)
        guard let minP = points.min(), let maxP = points.max() else { return (0, 1) }
        var lo = minP - 120, hi = maxP + 120
        if hi - lo < 20 * 60 { hi = lo + 20 * 60 }
        return (lo, hi)
    }

    private func x(_ sec: Int, in width: CGFloat) -> CGFloat {
        let w = window
        return CGFloat((Double(sec) - w.lo) / (w.hi - w.lo)) * width
    }

    private var bar: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.white.opacity(0.08)).frame(height: 3)
                if let t = targetSec {
                    Rectangle().fill(.white).frame(width: 1, height: 14)
                        .offset(x: x(t, in: geo.size.width))
                }
                if let p = prediction?.predictedSec {
                    Circle().fill(.white).frame(width: 8, height: 8)
                        .offset(x: x(p, in: geo.size.width) - 4)
                }
                if let c = corosSec {
                    Circle().stroke(.white, lineWidth: 1.5).frame(width: 8, height: 8)
                        .offset(x: x(c, in: geo.size.width) - 4)
                }
            }
            .frame(height: 14)
        }
        .frame(height: 14)
        .accessibilityHidden(true)
    }

    private var legend: some View {
        HStack(spacing: DS.Spacing.l) {
            if let mid = prediction?.predicted {
                HStack(spacing: DS.Spacing.xs) {
                    Circle().fill(.white).frame(width: 6, height: 6)
                    label("Phoenix \(mid)", color: DS.Colors.onSurface)
                }
            }
            if let c = corosText {
                HStack(spacing: DS.Spacing.xs) {
                    Circle().stroke(.white, lineWidth: 1.5).frame(width: 6, height: 6)
                    label("COROS \(c)", color: DS.Colors.onSurface)
                }
            }
        }
        .accessibilityHidden(true)
    }

    /// "Phoenix from 21.1 km in 1:38:48 on Mar 15, range 3:20:50–3:31:08.
    /// COROS tracking since Oct 9." — or the COROS change once two days differ.
    private var footnote: String {
        var parts: [String] = []
        if let p = prediction, p.predictedSec != nil {
            let km = p.basisKm.map { String(format: $0.truncatingRemainder(dividingBy: 1) == 0 ? "%.0f km" : "%.1f km", $0) } ?? "a race"
            var s = "Phoenix from \(km) in \(p.basisTime ?? "—") on \(Self.shortDate(p.basisDate))"
            if let lo = p.predictedLo, let hi = p.predictedHi { s += ", range \(lo)–\(hi)" }
            parts.append(s + ".")
        }
        if let c = coros, corosSec != nil {
            let delta = raceDistance == "Marathon" ? c.marathonDeltaSec : c.halfDeltaSec
            if let d = delta, let since = c.sinceDate, (c.daysTracked ?? 1) > 1 {
                parts.append("COROS \(Self.signedMinutes(d)) since \(Self.shortDate(since)).")
            } else if let date = c.date {
                parts.append("COROS tracking since \(Self.shortDate(date)).")
            }
        }
        return parts.joined(separator: " ")
    }

    private var accessibilityText: String {
        var s = "Predicted \(prediction?.distance ?? raceDistance ?? "race")."
        if let t = targetFinishTime { s += " Target \(t)." }
        if let p = prediction?.predicted { s += " Phoenix \(p)." }
        if let c = corosText { s += " COROS \(c)." }
        return s + " " + footnote
    }

    // MARK: Formatting

    static func parseHMS(_ text: String?) -> Int? {
        guard let text else { return nil }
        let parts = text.split(separator: ":").compactMap { Int($0) }
        switch parts.count {
        case 3: return parts[0] * 3600 + parts[1] * 60 + parts[2]
        case 2: return parts[0] * 60 + parts[1]
        default: return nil
        }
    }

    static func trimLeadingZero(_ hms: String) -> String {
        hms.hasPrefix("0") && hms.count > 1 ? String(hms.dropFirst()) : hms
    }

    /// −8:53 for -533 s; +0:12 for 12 s.
    static func signedMinutes(_ seconds: Int) -> String {
        let sign = seconds < 0 ? "−" : "+"
        let s = abs(seconds)
        return "\(sign)\(s / 60):\(String(format: "%02d", s % 60))"
    }

    /// "Oct 9" from an ISO date; explicit locale keeps the month English.
    static func shortDate(_ iso: String?) -> String {
        guard let iso else { return "—" }
        let parser = DateFormatter()
        parser.locale = Locale(identifier: "en_US_POSIX")
        parser.dateFormat = "yyyy-MM-dd"
        guard let date = parser.date(from: iso) else { return iso }
        let out = DateFormatter()
        out.locale = Locale(identifier: "en_US_POSIX")
        out.dateFormat = "MMM d"
        return out.string(from: date)
    }
}
