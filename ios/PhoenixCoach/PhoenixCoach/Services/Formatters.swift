import Foundation

enum Formatters {
    
    // Formatter: yyyy-MM-dd
    static let yyyyMMdd: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()
    
    // Formatter: EEEE, MMM d • h:mm a
    static let dashboardDate: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "EEEE, MMM d • h:mm a"
        return f
    }()
    
    // Formatter: EEEE, MMM d
    static let todayDate: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "EEEE, MMM d"
        return f
    }()
    
    // Formatter: short time (h:mm a)
    static let shortTime: DateFormatter = {
        let f = DateFormatter()
        f.timeStyle = .short
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
    
    // ISO8601
    static let iso8601: ISO8601DateFormatter = {
        return ISO8601DateFormatter()
    }()
}
