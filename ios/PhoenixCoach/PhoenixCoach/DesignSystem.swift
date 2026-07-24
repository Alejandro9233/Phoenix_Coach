import SwiftUI

enum DS {
    // MARK: - Colors
    enum Colors {
        static let background  = Color(red: 0.075, green: 0.075, blue: 0.082)
        static let surface     = Color(red: 0.122, green: 0.122, blue: 0.129)
        static let primaryText = Color(red: 0.784, green: 0.776, blue: 0.780)
        static let accent      = Color.white                                    // Pure white per user request
        static let outline     = Color(red: 0.569, green: 0.565, blue: 0.580)
        static let onSurface   = Color(red: 0.780, green: 0.776, blue: 0.792)
        static let success     = Color.green
        static let warning     = Color.orange                                   // Semantic warning
        static let danger      = Color.red                                      // Semantic danger
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
