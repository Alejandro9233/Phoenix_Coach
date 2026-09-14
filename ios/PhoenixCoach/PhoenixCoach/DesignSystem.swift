import SwiftUI

enum DS {
    // MARK: - Colors
    enum Colors {
        static let background  = Color(red: 0.075, green: 0.075, blue: 0.082)
        static let surface     = Color(red: 0.122, green: 0.122, blue: 0.129)
        static let primaryText = Color(red: 0.784, green: 0.776, blue: 0.780)
        static let accent      = Color.white                                    // Pure white per user request
        static let onAccent    = Color.black                                    // Text/spinners ON an accent fill — accent is white, so .white here is invisible
        static let outline     = Color(red: 0.569, green: 0.565, blue: 0.580)
        static let onSurface   = Color(red: 0.780, green: 0.776, blue: 0.792)
        static let success     = Color.green
        static let warning     = Color.orange                                   // Semantic warning
        static let danger      = Color.red                                      // Semantic danger

        /// Training zones — colour IS the meaning here (Z1 blue … Z5 red).
        /// From the Today Activities Card canvas; the one place zone colours
        /// live. Unknown zone reads as outline so a missing value is quiet.
        static func zone(_ zone: Int?) -> Color {
            switch zone {
            case 1: return Color(red: 0.431, green: 0.659, blue: 0.878)   // #6EA8E0
            case 2: return Color(red: 0.373, green: 0.788, blue: 0.541)   // #5FC98A
            case 3: return Color(red: 0.910, green: 0.773, blue: 0.278)   // #E8C547
            case 4: return Color(red: 0.941, green: 0.588, blue: 0.290)   // #F0964A
            case 5: return Color(red: 0.898, green: 0.388, blue: 0.420)   // #E5636B
            default: return outline
            }
        }
    }
    
    // MARK: - Spacing (4/8 grid)
    enum Spacing {
        static let xs: CGFloat = 4
        static let s: CGFloat  = 8
        static let m: CGFloat  = 12
        static let l: CGFloat  = 16
        static let xl: CGFloat = 20
        static let xxl: CGFloat = 24
        static let page: CGFloat = 16      // screen edge insets
        static let section: CGFloat = 24   // gap between cards
    }

    // MARK: - Corner Radii
    enum Radius {
        static let small: CGFloat = 8
        static let medium: CGFloat = 12
        static let large: CGFloat = 16
        static let xl: CGFloat = 20
    }
    
    // MARK: - Typography tracking
    enum Tracking {
        static let tight: CGFloat = 0.9
        static let normal: CGFloat = 1.1
        static let wide: CGFloat = 1.5
    }
    
    // MARK: - Animation
    enum Animation {
        static let quick = SwiftUI.Animation.spring(response: 0.15, dampingFraction: 0.8)
        static let normal = SwiftUI.Animation.spring(response: 0.3, dampingFraction: 0.8)
        static let slow = SwiftUI.Animation.spring(response: 0.6, dampingFraction: 0.8)
        /// Bouncier than `.normal` on purpose: a latch clicking over. Only for
        /// arming/disarming a gesture (TodayView's pull tiers).
        static let detent = SwiftUI.Animation.spring(response: 0.28, dampingFraction: 0.62)
        /// Ambient drift for Today's background light: a 12-second breath,
        /// forever. The one animation in the app that isn't a state change,
        /// by decision (2026-09-13). Barely there; off under Reduce Motion.
        static let ambient = SwiftUI.Animation.easeInOut(duration: 12).repeatForever(autoreverses: true)
    }
}

// MARK: - Extensions & Modifiers

// HEX Color Parser Helper Extension
extension Color {
    init(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let a, r, g, b: UInt64
        switch hex.count {
        case 3: // RGB (12-bit)
            (a, r, g, b) = (255, (int >> 8) * 17, (int >> 4 & 0xF) * 17, (int & 0xF) * 17)
        case 6: // RGB (24-bit)
            (a, r, g, b) = (255, int >> 16, int >> 8 & 0xFF, int & 0xFF)
        case 8: // ARGB (32-bit)
            (a, r, g, b) = (int >> 24, int >> 16 & 0xFF, int >> 8 & 0xFF, int & 0xFF)
        default:
            (a, r, g, b) = (1, 1, 1, 0)
        }
        self.init(
            .sRGB,
            red: Double(r) / 255,
            green: Double(g) / 255,
            blue: Double(b) / 255,
            opacity: Double(a) / 255
        )
    }
}

// Glassmorphism Card Wrapper (from ProfileView, standardizing to Radius.large)
struct GlassPanelCard<Content: View>: View {
    var content: Content
    
    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }
    
    var body: some View {
        content
            .padding(16)
            .background(.ultraThinMaterial)
            .clipShape(RoundedRectangle(cornerRadius: DS.Radius.large))
            .overlay(
                RoundedRectangle(cornerRadius: DS.Radius.large)
                    .stroke(
                        LinearGradient(
                            gradient: Gradient(colors: [
                                .white.opacity(0.12),
                                .white.opacity(0.04)
                            ]),
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        ),
                        lineWidth: 1
                    )
            )
    }
}

// Standard Glass Card Modifier (from TodayView, using Radius.large for consistency)
struct GlassCardModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(16)
            .background(.ultraThinMaterial)
            .clipShape(RoundedRectangle(cornerRadius: DS.Radius.large))
            .overlay(
                RoundedRectangle(cornerRadius: DS.Radius.large)
                    .stroke(
                        LinearGradient(
                            gradient: Gradient(colors: [
                                .white.opacity(0.12),
                                .white.opacity(0.04)
                            ]),
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        ),
                        lineWidth: 1
                    )
            )
    }
}

extension View {
    func glassCard() -> some View {
        self.modifier(GlassCardModifier())
    }
}

// MARK: - Today v2 constructs (references/today-v2.md)

/// The outline card: stroke only, no fill. Light and grain pass through.
/// Today's card kind; `.glassCard()` remains the card elsewhere until each
/// screen goes through its own /variants round.
struct OutlineCardModifier: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(DS.Spacing.l)
            .overlay(
                RoundedRectangle(cornerRadius: DS.Radius.large)
                    .stroke(Color.white.opacity(0.22), lineWidth: 1)
            )
    }
}

extension View {
    func outlineCard() -> some View { modifier(OutlineCardModifier()) }
}

extension DS.Colors {
    /// The readiness tint. White on green: colour only when something needs
    /// attention. Unknown reads as outline, never as a warning.
    static func readiness(_ status: String?) -> Color {
        switch status {
        case "green": return .white
        case "yellow": return warning
        case "red": return danger
        default: return outline
        }
    }
}

extension DS {
    /// Section header grammar: 11 bold uppercase, wide tracking, outline.
    struct SectionLabel: View {
        let text: String
        var color: Color = DS.Colors.outline
        var body: some View {
            Text(text)
                .font(.system(size: 11, weight: .bold))
                .textCase(.uppercase)
                .tracking(DS.Tracking.wide)
                .foregroundStyle(color)
        }
    }

    /// Mono micro-label: the corner-pinned readouts of the tone references.
    struct MonoLabel: View {
        let text: String
        var color: Color = DS.Colors.outline
        var body: some View {
            Text(text)
                .font(.system(size: 10, weight: .medium, design: .monospaced))
                .textCase(.uppercase)
                .tracking(DS.Tracking.normal)
                .foregroundStyle(color)
        }
    }

    /// Hero numeral with its unit at the baseline. Data wears thin weights.
    struct HeroNumeral: View {
        let value: String
        var unit: String? = nil
        var size: CGFloat = 36
        var body: some View {
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(value)
                    .font(.system(size: size, weight: .ultraLight))
                    .monospacedDigit()
                    .foregroundStyle(.white)
                if let unit {
                    Text(unit)
                        .font(.system(size: 13))
                        .foregroundStyle(DS.Colors.outline)
                }
            }
            .fixedSize()
        }
    }

    /// Thin progress bar: 3pt capsule, white on white 0.08.
    struct ThinBar: View {
        let fraction: Double
        var body: some View {
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.white.opacity(0.08))
                    Capsule().fill(.white)
                        .frame(width: geo.size.width * min(1, max(0, fraction)))
                }
            }
            .frame(height: 3)
        }
    }

    /// Today's light: a white radial rising from under the readiness ring.
    /// It belongs to the header, not the screen — place it as the content's
    /// top-aligned background so it scrolls away with the ring (Alex,
    /// 2026-09-13). It breathes: a few points of drift and a sliver of
    /// opacity over twelve seconds. Static under Reduce Motion.
    struct HorizonGlow: View {
        @Environment(\.accessibilityReduceMotion) private var reduceMotion
        @State private var breathing = false

        var body: some View {
            RadialGradient(colors: [.white.opacity(breathing ? 0.38 : 0.30), .clear],
                           center: UnitPoint(x: 0.5, y: breathing ? 0.30 : 0.33),
                           startRadius: 0, endRadius: breathing ? 460 : 430)
                .frame(height: 900)
                .allowsHitTesting(false)
                .onAppear {
                    guard !reduceMotion else { return }
                    withAnimation(DS.Animation.ambient) { breathing = true }
                }
        }
    }

    /// How the film grain behaves. `.embers` is Today's pick from the grain
    /// rounds of 2026-09-13; `.still` is the plain layer for anywhere else.
    enum GrainMode { case still, embers }

    /// Film grain: white dust over the background, seeded so a still frame
    /// never shimmers by accident. `.embers`: the dust is denser toward the
    /// bottom, and a few brighter specks are born in the bottom third and
    /// die as they climb — the ascending feel of the tone references.
    struct GrainOverlay: View {
        var mode: GrainMode = .still
        var count = 12000
        @Environment(\.accessibilityReduceMotion) private var reduceMotion

        var body: some View {
            Group {
                switch mode {
                case .still:
                    layer(seed: 42)
                case .embers:
                    ZStack {
                        layer(seed: 42, rising: true)
                        risingSpecks(count: 140, seconds: 24, band: 0.45)
                    }
                }
            }
            .allowsHitTesting(false)
            .ignoresSafeArea()
        }

        /// Specks that climb. Each has its own column, phase and speed from
        /// the seed; y wraps, alpha fades toward the top so nothing pops at
        /// the edge. Redrawn at 12 fps — a few hundred points, cheap. Frozen
        /// under Reduce Motion.
        private func risingSpecks(count: Int, seconds: Double, band: CGFloat) -> some View {
            TimelineView(.periodic(from: .now, by: reduceMotion ? 3600 : 1.0 / 12.0)) { ctx in
                let t = reduceMotion ? 0 : ctx.date.timeIntervalSinceReferenceDate
                Canvas { g, size in
                    var state: UInt64 = 9 &* 6364136223846793005 &+ 1442695040888963407
                    func next() -> CGFloat {
                        state = state &* 6364136223846793005 &+ 1442695040888963407
                        return CGFloat((state >> 33) & 0xFFFFFF) / CGFloat(0x1000000)
                    }
                    for _ in 0..<count {
                        let x = next() * size.width
                        let offset = next()
                        let speed = 0.6 + 0.8 * next()
                        let base = (0.05 + 0.10 * next()) * 2.2
                        let progress = (CGFloat(t / seconds) * speed + offset).truncatingRemainder(dividingBy: 1)
                        let y = size.height - progress * size.height * band
                        let a = base * (1 - progress)
                        g.fill(Path(ellipseIn: CGRect(x: x, y: y, width: 1.6, height: 1.6)),
                               with: .color(.white.opacity(min(1, a))))
                    }
                }
            }
        }

        private func layer(seed: UInt64, rising: Bool = false) -> some View {
            Canvas { ctx, size in
                var state: UInt64 = seed &* 6364136223846793005 &+ 1442695040888963407
                func next() -> CGFloat {
                    state = state &* 6364136223846793005 &+ 1442695040888963407
                    return CGFloat((state >> 33) & 0xFFFFFF) / CGFloat(0x1000000)
                }
                for _ in 0..<count {
                    let x = next() * size.width
                    var y = next() * size.height
                    var a = 0.03 + 0.07 * next()
                    if rising {
                        let r = next()
                        y = size.height * (1 - r * r)          // denser toward the bottom
                        a *= 0.3 + 0.9 * (y / size.height)     // brighter toward the bottom
                    }
                    ctx.fill(Path(CGRect(x: x, y: y, width: 1, height: 1)),
                             with: .color(.white.opacity(a)))
                }
            }
        }
    }
}

// MARK: - Grain mode in the environment (the /variants round switches it)

private struct GrainModeKey: EnvironmentKey {
    static let defaultValue: DS.GrainMode = .embers
}

extension EnvironmentValues {
    var grainMode: DS.GrainMode {
        get { self[GrainModeKey.self] }
        set { self[GrainModeKey.self] = newValue }
    }
}
