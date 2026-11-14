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
    /// Whole other meals for this slot (the chicken sandwich for the tostadas).
    let variants: [NutritionMealVariant]?
}

struct NutritionMealVariant: Codable {
    let name: String
    let items: [NutritionItem]
    let totals: NutritionTotals
    /// This variant minus the meal it replaces.
    let delta: NutritionTotals
}

struct NutritionItem: Codable {
    let foodId: String
    let name: String
    let amount: Double
    let unit: String
    let pieces: Double?
    let pieceName: String?
    /// "3 tostadas", "1 lata": only for a food eaten whole. Python words it.
    let piecesLabel: String?
    /// "raw" / "cooked" / "prepared": how the grams are weighed.
    let weigh: String?
    /// A swap at the given amount that needs no recalculation (amaranth for oats).
    let alternative: NutritionAlternative?
    /// Every swap for this food, the one above first. The backend checked each
    /// against the day's rules; picking one changes nothing that is stored.
    let alternatives: [NutritionSwap]?

    enum CodingKeys: String, CodingKey {
        case name, amount, unit, pieces, weigh, alternative, alternatives
        case foodId = "food_id"
        case pieceName = "piece_name"
        case piecesLabel = "pieces_label"
    }
}

struct NutritionAlternative: Codable {
    let name: String
    let amount: Double
    let unit: String
}

struct NutritionSwap: Codable {
    let name: String
    let amount: Double
    let unit: String
    let piecesLabel: String?
    /// The swap minus the food it replaces.
    let delta: NutritionTotals?
    /// Label numbers the package doubts, or micronutrients from a similar food.
    let estimate: Bool?

    enum CodingKeys: String, CodingKey {
        case name, amount, unit, delta, estimate
        case piecesLabel = "pieces_label"
    }
}

struct NutritionTotals: Codable {
    let kcal: Double?
    let proteinG: Double?
    let carbsG: Double?
    let fatG: Double?
    let fiberG: Double?

    enum CodingKeys: String, CodingKey {
        case kcal
        case proteinG = "protein_g"
        case carbsG = "carbs_g"
        case fatG = "fat_g"
        case fiberG = "fiber_g"
    }
}
