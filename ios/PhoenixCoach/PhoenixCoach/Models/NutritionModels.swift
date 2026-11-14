import Foundation

// MARK: - Nutrition (GET /nutrition/today, /nutrition/days)

/// One day of the nutrition package. The day type comes from the stored plan
/// (backend/services/nutrition.py); `source` is "calendar" when the week has
/// no plan yet or the date is one of the package's fixed days.
struct NutritionDay: Codable {
    let date: String
    let dayType: String?
    let dayName: String?
    let source: String?
    let reason: String?
    let event: String?
    let note: String?
    let totals: NutritionTotals?
    let meals: [NutritionMeal]?
    let supplements: [NutritionItem]?
    /// Every daily supplement with its dose, creatine and ashwagandha
    /// included (the menus only carry the ones with nutrients).
    let dailySupplements: [NutritionSupplement]?
    let nextMealIndex: Int?
    /// Set when tomorrow's long run, not today's session, picked the day type.
    let tomorrowLongRunMin: Int?

    /// No stored plan for the week: the calendar's guess, shown as such.
    var isEstimate: Bool { source == "calendar" && reason?.hasPrefix("No plan") == true }

    enum CodingKeys: String, CodingKey {
        case date, source, reason, event, note, totals, meals, supplements
        case dayType = "day_type"
        case dayName = "day_name"
        case nextMealIndex = "next_meal_index"
        case tomorrowLongRunMin = "tomorrow_long_run_min"
        case dailySupplements = "daily_supplements"
    }
}

struct NutritionSupplement: Codable {
    let id: String
    let name: String
    let dose: String?
}

struct NutritionMeal: Codable {
    let time: String
    let name: String
    let items: [NutritionItem]
    let totals: NutritionTotals
}

struct NutritionItem: Codable {
    let foodId: String
    let name: String
    let amount: Double
    let unit: String
    let pieces: Double?
    let pieceName: String?
    /// "raw" / "cooked" / "prepared": how the grams are weighed.
    let weigh: String?
    /// A swap at the given amount that needs no recalculation (amaranth for oats).
    let alternative: NutritionAlternative?

    enum CodingKeys: String, CodingKey {
        case name, amount, unit, pieces, weigh, alternative
        case foodId = "food_id"
        case pieceName = "piece_name"
    }
}

struct NutritionAlternative: Codable {
    let name: String
    let amount: Double
    let unit: String
}

struct NutritionTotals: Codable {
    let kcal: Double?
    let proteinG: Double?
    let carbsG: Double?
    let fatG: Double?

    enum CodingKeys: String, CodingKey {
        case kcal
        case proteinG = "protein_g"
        case carbsG = "carbs_g"
        case fatG = "fat_g"
    }
}
