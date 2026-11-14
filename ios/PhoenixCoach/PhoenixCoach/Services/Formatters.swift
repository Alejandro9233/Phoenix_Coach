import Foundation

enum Formatters {
    
    // Formatter: yyyy-MM-dd (fixed-format API dates, so POSIX locale)
    static let yyyyMMdd: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()
    
    // Formatter: MMM d
    static let shortMonthDay: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "MMM d"
        return f
    }()
    
    // Formatter: shortWeekdaySymbols
    static let shortWeekdaySymbols: [String] = {
        let f = DateFormatter()
        return f.shortWeekdaySymbols
    }()
    
}
