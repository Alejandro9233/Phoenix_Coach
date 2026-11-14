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

    /// Why the day type outranks today's session. Without it an easy run
    /// under "Fuel · D" read as a mistake.
    private var tomorrowLine: String? {
        day.tomorrowLongRunMin.map { "For tomorrow's \($0) min long run" }
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
                        if let tomorrowLine {
                            DS.MonoLabel(text: tomorrowLine, color: DS.Colors.onSurface)
                        }
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
        .accessibilityLabel("Fuel, day \(day.dayType ?? ""). \(tomorrowLine.map { "\($0). " } ?? "")\(title). \(detail)")
        .accessibilityHint("Opens today's full menu")
        .sheet(isPresented: $showMenu) { FuelSheet(day: day) }
    }
}

/// The whole day's menu as a timeline rail (fuel-sheet round 1, V1): time,
/// dot, meal. One meal open at a time, the next one to start (round 2: V4's
/// chevron + blur-in, with V2's accordion). Supplements close the day.
/// An open meal is a ledger (meal-detail round, V1, 2026-11-14): a macro
/// line, each food with its swaps as grey "o …" lines, and "Or instead" for
/// a whole other meal. Showing a variant is this sheet's state only; the
/// backend stores nothing and the day's totals stay the plan's.
struct FuelSheet: View {
    let day: NutritionDay
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var open: Int?
    /// Meal index → the variant shown in its place.
    @State private var shownVariant: [Int: Int] = [:]

    private var meals: [NutritionMeal] { day.meals ?? [] }

    init(day: NutritionDay) {
        self.day = day
        _open = State(initialValue: day.nextMealIndex)
    }

    var body: some View {
        NavigationStack {
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
            .navigationTitle("Fuel · Day \(day.dayType ?? "")")
            .navigationBarTitleDisplayMode(.inline)
            .glassSheet()
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
                    if day.tomorrowLongRunMin != nil {
                        // Tomorrow chose this menu: say it in a sentence, not a corner label.
                        Text(reason)
                            .font(.footnote)
                            .foregroundStyle(.white)
                            .fixedSize(horizontal: false, vertical: true)
                    } else {
                        DS.MonoLabel(text: day.source == "plan" ? "From today's plan · \(reason)" : reason)
                    }
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

    private func variant(_ i: Int, _ meal: NutritionMeal) -> NutritionMealVariant? {
        guard let v = shownVariant[i], let all = meal.variants, all.indices.contains(v) else { return nil }
        return all[v]
    }

    private func railRow(_ i: Int, _ meal: NutritionMeal) -> some View {
        let isOpen = open == i
        let shown = variant(i, meal)
        let totals = shown?.totals ?? meal.totals
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
                        Text(shown?.name ?? meal.name)
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundStyle(isPast ? DS.Colors.onSurface : .white)
                            .multilineTextAlignment(.leading)
                        Spacer(minLength: DS.Spacing.s)
                        if let k = totals.kcal { DS.MonoLabel(text: FuelFormat.kcal(k)).fixedSize() }
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
                .accessibilityLabel("\(meal.time), \(shown?.name ?? meal.name)\(i == next ? ", next" : "")")
                .accessibilityValue(isOpen ? "Expanded" : "Collapsed")
                if isOpen {
                    VStack(alignment: .leading, spacing: DS.Spacing.s) {
                        VStack(alignment: .leading, spacing: DS.Spacing.s) {
                            if let line = FuelFormat.macros(totals) { DS.MonoLabel(text: line) }
                            ForEach(Array((shown?.items ?? meal.items).enumerated()), id: \.offset) { _, item in itemLine(item) }
                        }
                        .id(shownVariant[i] ?? -1)
                        .transition(.opacity)
                        if let variants = meal.variants, !variants.isEmpty {
                            DS.Hairline().padding(.top, DS.Spacing.s)
                            if shown != nil {
                                insteadRow(meal.name, meal.totals, delta: nil) { shownVariant[i] = nil }
                            }
                            ForEach(Array(variants.enumerated()), id: \.offset) { v, other in
                                if shownVariant[i] != v {
                                    insteadRow(other.name, other.totals, delta: shown == nil ? other.delta.kcal : nil) {
                                        shownVariant[i] = v
                                    }
                                }
                            }
                        }
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

    /// A whole other meal for this slot: tap to read the menu with it.
    private func insteadRow(_ name: String, _ totals: NutritionTotals, delta: Double?,
                            show: @escaping () -> Void) -> some View {
        Button { withAnimation(DS.Animation.normal) { show() } } label: {
            HStack(alignment: .center, spacing: DS.Spacing.m) {
                VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                    DS.SectionLabel(text: "Or instead")
                    Text(name)
                        .font(.system(size: 13))
                        .foregroundStyle(.white)
                        .multilineTextAlignment(.leading)
                    if let line = FuelFormat.macros(totals) { DS.MonoLabel(text: line).lineLimit(1) }
                }
                .layoutPriority(1)
                Spacer(minLength: 0)
                if let k = totals.kcal {
                    DS.MonoLabel(text: [FuelFormat.kcal(k), delta.map(FuelFormat.signed)].compactMap { $0 }.joined(separator: " · "))
                        .fixedSize()
                }
                Image(systemName: "chevron.right")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(DS.Colors.outline)
            }
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityHint("Shows this meal in its place")
    }

    /// Grams lead. A food eaten whole leads with its pieces and keeps the grams beside them.
    private func amountText(_ amount: Double, _ unit: String, pieces: String?) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: DS.Spacing.xs) {
            if let pieces {
                Text(pieces).font(.system(size: 13)).foregroundStyle(.white)
                Text("\(FuelFormat.amount(amount)) \(unit)").font(.system(size: 11)).foregroundStyle(DS.Colors.outline)
            } else {
                Text("\(FuelFormat.amount(amount)) \(unit)").font(.system(size: 13)).foregroundStyle(.white)
            }
        }
        .monospacedDigit()
        .fixedSize()
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
                amountText(item.amount, item.unit, pieces: item.piecesLabel)
            }
            if let swaps = item.alternatives {
                ForEach(Array(swaps.enumerated()), id: \.offset) { _, swap in
                    HStack(alignment: .firstTextBaseline, spacing: DS.Spacing.s) {
                        Text("o \(swap.name) \(swap.piecesLabel ?? "\(FuelFormat.amount(swap.amount)) \(swap.unit)")")
                            .font(.system(size: 11))
                            .foregroundStyle(DS.Colors.outline)
                            .lineLimit(1)
                        Spacer(minLength: DS.Spacing.s)
                        if let k = swap.delta?.kcal {
                            DS.MonoLabel(text: "\(FuelFormat.signed(k)) kcal\(swap.estimate == true ? " · est" : "")")
                                .fixedSize()
                        }
                    }
                }
            } else if let alt = item.alternative {
                // a backend from before the swaps list
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

    /// 80 → "+80", -41 → "−41", 0 → "±0".
    static func signed(_ v: Double) -> String {
        let n = Int(v.rounded())
        return n == 0 ? "±0" : (n > 0 ? "+\(n)" : "−\(-n)")
    }

    /// A meal's macro line: protein, fat and fiber (Alex, 2026-11-14: fiber
    /// instead of carbs). A missing figure is left out, never shown as 0.
    static func macros(_ t: NutritionTotals) -> String? {
        let parts = [("P", t.proteinG), ("Fat", t.fatG), ("Fiber", t.fiberG)].compactMap { label, v in
            v.map { "\(label) \(Int($0.rounded())) g" }
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}
