import SwiftUI
import Charts

/// One recovery metric over time (HRV, resting HR, load ratio). Opened from
/// the readiness readouts on Today.
enum MetricType {
    case hrv, rhr, load
}

struct MetricChartSheet: View {
    let title: String
    let data: [RecoverySnapshot]
    let metricType: MetricType
    
    // Sort chronological and take the last 7
    private var chartData: [RecoverySnapshot] {
        let sorted = data.sorted { ($0.date ?? "") < ($1.date ?? "") }
        return Array(sorted.suffix(7))
    }
    
    var body: some View {
        NavigationStack {
            ZStack {
                VStack(spacing: 24) {
                    if chartData.isEmpty {
                        ContentUnavailableView("No Data", systemImage: "chart.xyaxis.line")
                    } else {
                        Chart {
                            ForEach(chartData, id: \.id) { item in
                                if let value = value(for: item) {
                                    LineMark(
                                        x: .value("Day", dateString(for: item) ?? "?"),
                                        y: .value(title, value)
                                    )
                                    .interpolationMethod(.catmullRom)
                                    .foregroundStyle(DS.Colors.accent)
                                    .symbol(Circle())
                                }
                            }
                        }
                        .chartYAxis {
                            AxisMarks(position: .leading)
                        }
                        .frame(height: 250)
                        .padding()
                        .glassCard()
                    }
                    Spacer()
                }
                .padding(24)
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .glassSheet()
        }
        .presentationDetents([.medium])
        .presentationDragIndicator(.visible)
    }
    
    private func value(for item: RecoverySnapshot) -> Double? {
        switch metricType {
        case .hrv: return item.hrvMs
        case .rhr: return item.restingHr.map { Double($0) }
        case .load: return item.loadRatio
        }
    }
    
    private func dateString(for item: RecoverySnapshot) -> String? {
        guard let dateStr = item.date else { return nil }
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        guard let date = formatter.date(from: dateStr) else { return nil }
        formatter.dateFormat = "E" // short day like Mon, Tue
        return formatter.string(from: date)
    }
}
