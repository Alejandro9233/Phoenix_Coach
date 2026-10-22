import SwiftUI

/// Today's fuel: one hairline row under the session — day type, next meal,
/// kcal — that opens the full day's menu. Nutrition round, V3 (2026-10-21).
/// Read-only; the day type is Python's (services/nutrition.py), never ours.
struct FuelRow: View {
    let day: NutritionDay
    @State private var showMenu = false

    private var next: NutritionMeal? {
        guard let i = day.nextMealIndex, let meals = day.meals, meals.indices.contains(i) else { return nil }
        return meals[i]
    }

    private var title: String {
        guard let next else { return "Day done" }
        return "\(next.time) · \(next.name)"
    }

    private var detail: String {
        var parts: [String] = []
        if let k = next?.totals.kcal { parts.append("\(FuelFormat.kcal(k)) kcal") }
        if let k = day.totals?.kcal { parts.append("day \(FuelFormat.kcal(k))") }
        if day.isEstimate { parts.append("estimate") }
        return parts.joined(separator: " · ")
    }

    var body: some View {
        Button { showMenu = true } label: {
            VStack(spacing: DS.Spacing.s) {
                DS.Hairline()
                HStack(spacing: DS.Spacing.m) {
                    DS.SectionLabel(text: "Fuel · \(day.dayType ?? "")")
                    VStack(alignment: .leading, spacing: 2) {
                        Text(title)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(.white)
                            .lineLimit(1)
                        DS.MonoLabel(text: detail)
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(DS.Colors.outline)
                }
                .frame(minHeight: 44)
                DS.Hairline()
            }
            .padding(.horizontal, DS.Spacing.xs)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Fuel, day \(day.dayType ?? ""). \(title). \(detail)")
        .accessibilityHint("Opens today's full menu")
        .sheet(isPresented: $showMenu) { FuelSheet(day: day) }
    }
}

/// The whole day's menu as a timeline rail (fuel-sheet round 1, V1): time,
/// dot, meal. One meal open at a time, the next one to start (round 2: V4's
/// chevron + blur-in, with V2's accordion). Supplements close the day.
struct FuelSheet: View {
    let day: NutritionDay
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var open: Int?

    private var meals: [NutritionMeal] { day.meals ?? [] }

    init(day: NutritionDay) {
        self.day = day
        _open = State(initialValue: day.nextMealIndex)
    }

    var body: some View {
        NavigationStack {
            ZStack {
                DS.Colors.background.ignoresSafeArea()
                ScrollView(showsIndicators: false) {
                    VStack(alignment: .leading, spacing: DS.Spacing.xxl) {
                        header
                        VStack(alignment: .leading, spacing: 0) {
                            ForEach(Array(meals.enumerated()), id: \.offset) { i, meal in
                                railRow(i, meal)
                            }
                        }
                        if let sups = day.dailySupplements, !sups.isEmpty {
                            VStack(alignment: .leading, spacing: DS.Spacing.m) {
                                DS.SectionLabel(text: "Supplements")
                                ForEach(sups, id: \.id) { sup in supplementLine(sup) }
                            }
                        }
                    }
                    .padding(.horizontal, DS.Spacing.page)
                    .padding(.vertical, DS.Spacing.l)
                }
            }
            .navigationTitle("Fuel · Day \(day.dayType ?? "")")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { dismiss() } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(DS.Colors.outline)
                            .frame(minWidth: 44, minHeight: 44)
                            .contentShape(Rectangle())
                    }
                    .accessibilityLabel("Close")
                }
            }
        }
        .presentationDetents([.fraction(0.88), .large])
        .presentationDragIndicator(.visible)
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: DS.Spacing.m) {
            Text(day.dayType ?? "")
                .font(.system(size: 36, weight: .ultraLight))
                .foregroundStyle(.white)
            VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                Text(day.dayName ?? "")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.white)
                DS.MonoLabel(text: totalsLine)
                if let reason = day.reason {
                    DS.MonoLabel(text: day.source == "plan" ? "From today's plan · \(reason)" : reason)
                }
                if let note = day.note {
                    Text(note)
                        .font(.footnote)
                        .foregroundStyle(DS.Colors.onSurface)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private var totalsLine: String {
        guard let t = day.totals else { return "" }
        var parts: [String] = []
        if let k = t.kcal { parts.append("\(FuelFormat.kcal(k)) kcal") }
        for (label, value) in [("P", t.proteinG), ("C", t.carbsG), ("F", t.fatG)] {
            if let v = value { parts.append("\(label) \(FuelFormat.kcal(v)) g") }
        }
        return parts.joined(separator: " · ")
    }

    private func toggle(_ i: Int) {
        withAnimation(DS.Animation.normal) { open = open == i ? nil : i }
    }

    private func railRow(_ i: Int, _ meal: NutritionMeal) -> some View {
        let isOpen = open == i
        let next = day.nextMealIndex
        let isPast = next.map { i < $0 } ?? true
        return HStack(alignment: .top, spacing: DS.Spacing.m) {
            DS.MonoLabel(text: meal.time, color: i == next ? .white : DS.Colors.outline)
                .frame(width: 64, alignment: .leading)
                .padding(.top, headerInset + 3)
            VStack(spacing: 0) {
                Circle()
                    .fill(i == next ? Color.white : (isPast ? Color.white.opacity(0.5) : DS.Colors.background))
                    .overlay(Circle().stroke(Color.white.opacity(0.5), lineWidth: 1))
                    .frame(width: 8, height: 8)
                    .padding(.top, headerInset + 6)
                if i < meals.count - 1 {
                    Rectangle().fill(Color.white.opacity(0.12)).frame(width: 1)
                }
            }
            .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 0) {
                Button { toggle(i) } label: {
                    HStack(alignment: .firstTextBaseline, spacing: DS.Spacing.s) {
                        Text(meal.name)
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundStyle(isPast ? DS.Colors.onSurface : .white)
                            .multilineTextAlignment(.leading)
                        Spacer(minLength: DS.Spacing.s)
                        if let k = meal.totals.kcal { DS.MonoLabel(text: FuelFormat.kcal(k)) }
                        Image(systemName: "chevron.down")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(DS.Colors.outline)
                            .rotationEffect(.degrees(isOpen ? 180 : 0))
                    }
                    // The 44pt target is padding around the name, not
                    // space under it, so an open meal's foods sit close.
                    .padding(.vertical, headerInset)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(meal.time), \(meal.name)\(i == next ? ", next" : "")")
                .accessibilityValue(isOpen ? "Expanded" : "Collapsed")
                if isOpen {
                    VStack(alignment: .leading, spacing: DS.Spacing.s) {
                        ForEach(Array(meal.items.enumerated()), id: \.offset) { _, item in itemLine(item) }
                    }
                    .transition(reduceMotion
                        ? .opacity
                        : .modifier(active: BlurIn(radius: 6), identity: BlurIn(radius: 0)))
                }
            }
            .padding(.bottom, isOpen ? DS.Spacing.l : 0)
        }
        .clipped()
    }

    private let headerInset = DS.Spacing.m

    private func supplementLine(_ sup: NutritionSupplement) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(sup.name)
                .font(.system(size: 13))
                .foregroundStyle(.white)
            if let dose = sup.dose {
                Text(dose)
                    .font(.system(size: 11))
                    .foregroundStyle(DS.Colors.outline)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private func itemLine(_ item: NutritionItem) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: DS.Spacing.s) {
                Text(item.name)
                    .font(.system(size: 13))
                    .foregroundStyle(DS.Colors.onSurface)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: DS.Spacing.s)
                if let w = item.weigh { DS.MonoLabel(text: w) }
                Text("\(FuelFormat.amount(item.amount)) \(item.unit)")
                    .font(.system(size: 13))
                    .monospacedDigit()
                    .foregroundStyle(.white)
            }
            if let alt = item.alternative {
                Text("o \(alt.name) \(FuelFormat.amount(alt.amount)) \(alt.unit)")
                    .font(.system(size: 11))
                    .foregroundStyle(DS.Colors.outline)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// Arrives blurred and clear-faded, sharpens in place (fuel-sheet round 2, V4).
private struct BlurIn: ViewModifier {
    let radius: CGFloat
    func body(content: Content) -> some View {
        content.blur(radius: radius).opacity(radius == 0 ? 1 : 0)
    }
}

enum FuelFormat {
    private static let grouping: NumberFormatter = {
        let f = NumberFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.numberStyle = .decimal
        f.usesGroupingSeparator = true
        f.groupingSeparator = ","
        f.maximumFractionDigits = 0
        return f
    }()

    static func kcal(_ v: Double) -> String { grouping.string(from: NSNumber(value: v.rounded())) ?? "\(Int(v))" }

    /// 54 → "54", 0.5 → "0.5".
    static func amount(_ v: Double) -> String {
        v == v.rounded() ? String(Int(v)) : String(format: "%.1f", v)
    }
}
