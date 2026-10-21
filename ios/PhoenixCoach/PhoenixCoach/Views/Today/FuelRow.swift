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

/// The whole day's menu: meals in time order, grams, raw/cooked tags.
struct FuelSheet: View {
    let day: NutritionDay
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ZStack {
                DS.Colors.background.ignoresSafeArea()
                ScrollView(showsIndicators: false) {
                    VStack(alignment: .leading, spacing: DS.Spacing.section) {
                        header
                        ForEach(Array((day.meals ?? []).enumerated()), id: \.offset) { i, meal in
                            mealCard(meal, isNext: i == day.nextMealIndex)
                        }
                        if let sups = day.supplements, !sups.isEmpty {
                            itemsCard(label: "Supplements", items: sups)
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
        VStack(alignment: .leading, spacing: DS.Spacing.m) {
            HStack(alignment: .firstTextBaseline, spacing: DS.Spacing.m) {
                Text(day.dayType ?? "")
                    .font(.system(size: 36, weight: .ultraLight))
                    .foregroundStyle(.white)
                Text(day.dayName ?? "")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.white)
            }
            if let reason = day.reason {
                DS.MonoLabel(text: day.source == "plan" ? "From today's plan · \(reason)" : reason)
            }
            if let t = day.totals {
                HStack(alignment: .firstTextBaseline, spacing: DS.Spacing.m) {
                    if let k = t.kcal { DS.HeroNumeral(value: FuelFormat.kcal(k), unit: "kcal", size: 22) }
                    Spacer(minLength: 0)
                    DS.MonoLabel(text: [("P", t.proteinG), ("C", t.carbsG), ("F", t.fatG)]
                        .compactMap { l, v in v.map { "\(l) \(FuelFormat.amount($0)) g" } }
                        .joined(separator: " · "))
                }
            }
            if let note = day.note {
                Text(note)
                    .font(.footnote)
                    .foregroundStyle(DS.Colors.onSurface)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }

    private func mealCard(_ meal: NutritionMeal, isNext: Bool) -> some View {
        VStack(alignment: .leading, spacing: DS.Spacing.m) {
            HStack(alignment: .firstTextBaseline, spacing: DS.Spacing.s) {
                DS.MonoLabel(text: meal.time, color: .white)
                if isNext { DS.MonoLabel(text: "Next") }
                Spacer(minLength: 0)
                if let k = meal.totals.kcal { DS.MonoLabel(text: "\(FuelFormat.kcal(k)) kcal") }
            }
            Text(meal.name)
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(.white)
                .fixedSize(horizontal: false, vertical: true)
            itemRows(meal.items)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
        .accessibilityElement(children: .combine)
    }

    private func itemsCard(label: String, items: [NutritionItem]) -> some View {
        VStack(alignment: .leading, spacing: DS.Spacing.m) {
            DS.SectionLabel(text: label)
            itemRows(items)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard()
    }

    private func itemRows(_ items: [NutritionItem]) -> some View {
        VStack(alignment: .leading, spacing: DS.Spacing.s) {
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
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
            }
        }
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
