import SwiftUI

struct ProfileView: View {
    @ObservedObject private var network = NetworkManager.shared

    // Design system colors matching Quiet Performance HTML mockup







    @State private var backendURLText = ""
    
    @State private var profile: AthleteProfile = AthleteProfile(
        name: "", age: 30, weightKg: 75.0,
        raceName: "", raceType: "Triathlon", raceDistance: "70.3", raceDate: nil,
        swimDays: "wed,sat,sun",
        bikeDays: "mon,tue,wed,thu,fri,sat,sun",
        runDays: "mon,tue,wed,thu,fri,sat,sun",
        strengthDays: "mon,wed,fri"
    )
    // Form State
    @State private var hasRaceDate: Bool = false
    @State private var raceDateVal: Date = Date()
    @State private var trainingStartDateVal: Date = Date()
    
    // Notification Preferences
    @AppStorage("notifyMorningReadiness") private var notifyMorningReadiness: Bool = true
    @AppStorage("notifyCoachAnalysis") private var notifyCoachAnalysis: Bool = true
    @AppStorage("notifyLoadAlerts") private var notifyLoadAlerts: Bool = true
    @AppStorage("notifyRaceCountdown") private var notifyRaceCountdown: Bool = true
    
    // UI State
    @State private var hasTrainingStartDate = false
    @State private var targetHours = 3
    @State private var targetMinutes = 45
    @State private var targetSeconds = 0
    @State private var showDurationPicker = false
    @State private var showAgePicker = false
    @State private var showWeightPicker = false
    @State private var tempWeightInt = 78
    @State private var tempWeightDec = 5
    @State private var isLoading = false
    @State private var isSaving = false
    @State private var saveSuccess = false
    @State private var errorMessage: String?
    
    @State private var selectedSwimDays: Set<String> = []
    @State private var selectedBikeDays: Set<String> = []
    @State private var selectedRunDays: Set<String> = []
    @State private var selectedStrengthDays: Set<String> = []
    
    @FocusState private var activeField: Field?
    
    enum Field: Hashable {
        case name, age, weight, trainingStartDate, raceName, raceDate, targetTime, serverURL
    }
    
    let daysOfWeek = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    let dayLabels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    
    let raceTypes = ["Triathlon", "Running", "Cycling", "Swimming"]
    var distancesForType: [String] {
        switch profile.raceType ?? "Triathlon" {
        case "Running": return ["5k", "10k", "Half Marathon", "Marathon", "Ultra"]
        case "Cycling": return ["Time Trial", "Criterium", "Road Race", "Gravel", "Century"]
        case "Swimming": return ["50m", "100m", "200m", "400m", "800m", "1500m", "Open Water"]
        default: return ["Sprint", "Olympic", "70.3", "Ironman"]
        }
    }
    
    var body: some View {
        NavigationStack {
            ZStack {
                // Obsidian Dark Background
                DS.Colors.background
                    .ignoresSafeArea()
                
                RadialGradient(
                    gradient: Gradient(colors: [
                        DS.Colors.accent.opacity(0.12),
                        .clear
                    ]),
                    center: .top,
                    startRadius: 0,
                    endRadius: 400
                )
                .ignoresSafeArea()
                
                ScrollView {
                    VStack(spacing: 24) {
                        // Header Section
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Profile Settings")
                                .font(.system(size: 30, weight: .light))
                                .foregroundStyle(.white)
                                .tracking(-0.5)
                            
                            Text("Configure your parameters for the coaching algorithm.")
                                .font(.system(size: 15, weight: .light))
                                .foregroundStyle(DS.Colors.onSurface)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.top, 8)
                        
                        if isLoading {
                            VStack(spacing: 16) {
                                ProgressView()
                                    .tint(DS.Colors.accent)
                                Text("Loading athlete profile...")
                                    .font(.system(size: 14, weight: .light))
                                    .foregroundStyle(DS.Colors.outline)
                            }
                            .padding(80)
                            .frame(maxWidth: .infinity)
                        } else {
                            
                            // General Profile Telemetry Card
                            generalTelemetrySection
                            
                            // Race Objectives Board Card
                            raceObjectivesSection
                            
                            // Weekly Constraints Availability Matrix Card
                            weeklyConstraintsSection
                            
                            // Notifications Section
                            notificationsSection
                            
                            // Injury Log Link
                            NavigationLink(destination: InjuryLogView()) {
                                HStack {
                                    Image(systemName: "cross.case.fill")
                                        .font(.system(size: 14))
                                        .foregroundStyle(DS.Colors.accent)
                                    Text("Injury History")
                                        .font(.system(size: 13, weight: .bold))
                                        .foregroundStyle(.white)
                                    Spacer()
                                    Image(systemName: "chevron.right")
                                        .font(.system(size: 12, weight: .semibold))
                                        .foregroundStyle(DS.Colors.outline)
                                }
                                .padding()
                                .background(Color.white.opacity(0.04))
                                .clipShape(RoundedRectangle(cornerRadius: 12))
                                .overlay(
                                    RoundedRectangle(cornerRadius: 12)
                                        .strokeBorder(Color.white.opacity(0.08), lineWidth: 1)
                                )
                            }
                            .buttonStyle(.plain)
                            .padding(.top, 8)
                            .padding(.bottom, 40)
                            
                        }
                    }
                    .padding(.horizontal, 20)
                    .padding(.top, 10)
                }
                .scrollIndicators(.hidden)
            }
            .preferredColorScheme(.dark)
            .navigationTitle("Profile")
            .navigationBarTitleDisplayMode(.inline)
        }
        .alert("Profile Error", isPresented: Binding(
                get: { errorMessage != nil },
                set: { if !$0 { errorMessage = nil } }
            )) {
                Button("OK", role: .cancel) { }
            } message: {
                if let msg = errorMessage {
                    Text(msg)
                }
            }
        .task {
            backendURLText = network.baseURL
            await loadProfile()
        }
        .onAppear {
            backendURLText = network.baseURL
        }
    }
    
    // MARK: - Sections
    

    
    // GENERAL PROFILE TELEMETRY
    private var generalTelemetrySection: some View {
        GlassPanelCard {
            VStack(alignment: .leading, spacing: 20) {
                // Section Header
                HStack(spacing: 8) {
                    Image(systemName: "slider.horizontal.3")
                        .font(.system(size: 15))
                        .foregroundStyle(DS.Colors.outline)
                    Text("GENERAL TELEMETRY")
                        .font(.system(size: 11, weight: .bold))
                        .tracking(1.5)
                        .foregroundStyle(.white)
                }
                .padding(.bottom, 2)
                
                // Name
                SubtleUnderlineField(
                    label: "Name",
                    text: Binding(get: { profile.name ?? "" }, set: { profile.name = $0.isEmpty ? nil : $0 }),
                    placeholder: "Alex Runner",
                    isFocused: activeField == .name
                )
                .focused($activeField, equals: .name)
                

                // Age & Weight Column Grid (Tap to open Dropdown-style Pickers)
                HStack(spacing: 24) {
                    // Age Button
                    Button {
                        withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) {
                            showAgePicker.toggle()
                            showWeightPicker = false
                            showDurationPicker = false
                            activeField = showAgePicker ? .age : nil
                        }
                    } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Age")
                                .font(.system(size: 10, weight: .bold))
                                .foregroundStyle(showAgePicker ? DS.Colors.accent : DS.Colors.outline)
                                .tracking(1.5)
                                .textCase(.uppercase)
                            
                            HStack {
                                Text(profile.age != nil ? "\(profile.age!) yrs" : "Select Age")
                                    .font(.system(size: 17, weight: .light))
                                    .foregroundStyle(.white)
                                Spacer()
                                Image(systemName: "chevron.down")
                                    .font(.system(size: 12, weight: .semibold))
                                    .foregroundStyle(showAgePicker ? DS.Colors.accent : DS.Colors.outline)
                            }
                            .padding(.vertical, 4)
                            
                            Rectangle()
                                .frame(height: 1)
                                .foregroundStyle(showAgePicker ? DS.Colors.accent : Color.white.opacity(0.1))
                        }
                    }
                    .buttonStyle(.plain)
                    .focused($activeField, equals: .age)
                    
                    // Weight Button
                    Button {
                        if let w = profile.weightKg {
                            tempWeightInt = Int(w)
                            tempWeightDec = Int(round((w - Double(tempWeightInt)) * 10))
                        }
                        withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) {
                            showWeightPicker.toggle()
                            showAgePicker = false
                            showDurationPicker = false
                            activeField = showWeightPicker ? .weight : nil
                        }
                    } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Weight (KG)")
                                .font(.system(size: 10, weight: .bold))
                                .foregroundStyle(showWeightPicker ? DS.Colors.accent : DS.Colors.outline)
                                .tracking(1.5)
                                .textCase(.uppercase)
                            
                            HStack {
                                Text(profile.weightKg != nil ? String(format: "%.1f kg", profile.weightKg!) : "Select Weight")
                                    .font(.system(size: 17, weight: .light))
                                    .foregroundStyle(.white)
                                Spacer()
                                Image(systemName: "chevron.down")
                                    .font(.system(size: 12, weight: .semibold))
                                    .foregroundStyle(showWeightPicker ? DS.Colors.accent : DS.Colors.outline)
                            }
                            .padding(.vertical, 4)
                            
                            Rectangle()
                                .frame(height: 1)
                                .foregroundStyle(showWeightPicker ? DS.Colors.accent : Color.white.opacity(0.1))
                        }
                    }
                    .buttonStyle(.plain)
                    .focused($activeField, equals: .weight)
                }
                
                // Expandable Age Picker Wheels
                if showAgePicker {
                    VStack(spacing: 8) {
                        HStack {
                            Text("Select Age")
                                .font(.system(size: 10, weight: .bold))
                                .foregroundStyle(DS.Colors.outline)
                                .tracking(1.0)
                            Spacer()
                            Button("Done") {
                                withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) {
                                    showAgePicker = false
                                    activeField = nil
                                }
                                Task { await saveProfile() }
                            }
                            .font(.system(size: 11, weight: .bold))
                            .foregroundStyle(DS.Colors.accent)
                        }
                        .padding(.horizontal, 12)
                        .padding(.top, 8)
                        
                        Picker("Age", selection: Binding(
                            get: { profile.age ?? 28 },
                            set: { profile.age = $0 }
                        )) {
                            ForEach(10..<100) { age in
                                Text("\(age) yrs").tag(age)
                            }
                        }
                        .pickerStyle(.wheel)
                        .frame(height: 110)
                    }
                    .background(Color.white.opacity(0.02))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12)
                            .stroke(Color.white.opacity(0.08), lineWidth: 1)
                    )
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .padding(.top, 4)
                }
                
                // Expandable Weight Picker Wheels
                if showWeightPicker {
                    VStack(spacing: 8) {
                        HStack {
                            Text("Select Weight")
                                .font(.system(size: 10, weight: .bold))
                                .foregroundStyle(DS.Colors.outline)
                                .tracking(1.0)
                            Spacer()
                            Button("Done") {
                                let finalWeight = Double(tempWeightInt) + (Double(tempWeightDec) / 10.0)
                                profile.weightKg = finalWeight
                                withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) {
                                    showWeightPicker = false
                                    activeField = nil
                                }
                                Task { await saveProfile() }
                            }
                            .font(.system(size: 11, weight: .bold))
                            .foregroundStyle(DS.Colors.accent)
                        }
                        .padding(.horizontal, 12)
                        .padding(.top, 8)
                        
                        HStack(spacing: 0) {
                            Picker("Integer", selection: $tempWeightInt) {
                                ForEach(30..<201) { w in
                                    Text("\(w)").tag(w)
                                }
                            }
                            .pickerStyle(.wheel)
                            .frame(minWidth: 50)
                            
                            Text(".")
                                .font(.system(size: 24, weight: .bold))
                                .foregroundStyle(.white)
                            
                            Picker("Decimal", selection: $tempWeightDec) {
                                ForEach(0..<10) { d in
                                    Text("\(d)").tag(d)
                                }
                            }
                            .pickerStyle(.wheel)
                            .frame(minWidth: 50)
                            
                            Text("kg")
                                .font(.system(size: 16, weight: .medium))
                                .foregroundStyle(DS.Colors.outline)
                                .padding(.trailing, 8)
                        }
                        .frame(height: 110)
                    }
                    .background(Color.white.opacity(0.02))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12)
                            .stroke(Color.white.opacity(0.08), lineWidth: 1)
                    )
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .padding(.top, 4)
                }
            }
        }
    }
    
    // RACE OBJECTIVE BOARD
    private var raceObjectivesSection: some View {
        GlassPanelCard {
            VStack(alignment: .leading, spacing: 20) {
                // Section Header
                HStack(spacing: 8) {
                    Image(systemName: "flag.fill")
                        .font(.system(size: 15))
                        .foregroundStyle(DS.Colors.outline)
                    Text("RACE OBJECTIVE")
                        .font(.system(size: 11, weight: .bold))
                        .tracking(1.5)
                        .foregroundStyle(.white)
                }
                .padding(.bottom, 2)
                
                // Race Name
                SubtleUnderlineField(
                    label: "Race Name",
                    text: Binding(get: { profile.raceName ?? "" }, set: { profile.raceName = $0.isEmpty ? nil : $0 }),
                    placeholder: "Berlin Marathon",
                    isFocused: activeField == .raceName
                )
                .focused($activeField, equals: .raceName)
                .onSubmit {
                    Task { await saveProfile() }
                }
                
                // Segmented Selector for Race Type (with auto-save on select!)
                SegmentedSelector(selectedType: Binding(
                    get: { profile.raceType ?? "Triathlon" },
                    set: { newType in
                        profile.raceType = newType
                        switch newType {
                        case "Running": profile.raceDistance = "Marathon"
                        case "Cycling": profile.raceDistance = "Road Race"
                        case "Swimming": profile.raceDistance = "1500m"
                        default: profile.raceDistance = "70.3"
                        }
                        Task { await saveProfile() }
                    }
                ))
                
                // Distance Menu Dropdown (with auto-save on select!)
                ElegantDropdownField(
                    label: "Distance",
                    selection: Binding(
                        get: { profile.raceDistance ?? distancesForType.first ?? "" },
                        set: { 
                            profile.raceDistance = $0
                            Task { await saveProfile() }
                        }
                    ),
                    options: distancesForType
                )
                
                // Dates Row
                HStack(spacing: 24) {
                    // Training Start Date
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Start Date")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundStyle(activeField == .trainingStartDate ? DS.Colors.accent : DS.Colors.outline)
                            .tracking(1.5)
                            .textCase(.uppercase)
                        
                        DatePicker("", selection: $trainingStartDateVal, displayedComponents: .date)
                            .labelsHidden()
                            .datePickerStyle(.compact)
                            .tint(DS.Colors.accent)
                            .padding(.vertical, 2)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .onAppear { hasTrainingStartDate = true }
                            .onChange(of: trainingStartDateVal) { _ in Task { await saveProfile() } }
                        
                        Rectangle()
                            .frame(height: 1)
                            .foregroundStyle(activeField == .trainingStartDate ? DS.Colors.accent : Color.white.opacity(0.1))
                    }
                    .focused($activeField, equals: .trainingStartDate)
                    
                    // Race Date
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Race Date")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundStyle(activeField == .raceDate ? DS.Colors.accent : DS.Colors.outline)
                            .tracking(1.5)
                            .textCase(.uppercase)
                        
                        DatePicker("", selection: $raceDateVal, displayedComponents: .date)
                            .labelsHidden()
                            .datePickerStyle(.compact)
                            .tint(DS.Colors.accent)
                            .padding(.vertical, 2)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .onAppear { hasRaceDate = true }
                            .onChange(of: raceDateVal) { _ in Task { await saveProfile() } }
                        
                        Rectangle()
                            .frame(height: 1)
                            .foregroundStyle(activeField == .raceDate ? DS.Colors.accent : Color.white.opacity(0.1))
                    }
                    .focused($activeField, equals: .raceDate)
                }
                
                // Target Finish Time (Tap to open Duration Picker)
                Button {
                    withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) {
                        showDurationPicker.toggle()
                        showAgePicker = false
                        showWeightPicker = false
                        activeField = showDurationPicker ? .targetTime : nil
                    }
                } label: {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Target Time")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundStyle(showDurationPicker ? DS.Colors.accent : DS.Colors.outline)
                            .tracking(1.5)
                            .textCase(.uppercase)
                        
                        HStack {
                            Text(String(format: "%02dh %02dm %02ds", targetHours, targetMinutes, targetSeconds))
                                .font(.system(size: 17, weight: .light))
                                .foregroundStyle(.white)
                            Spacer()
                            Image(systemName: "chevron.down")
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundStyle(showDurationPicker ? DS.Colors.accent : DS.Colors.outline)
                        }
                        .padding(.vertical, 4)
                        
                        Rectangle()
                            .frame(height: 1)
                            .foregroundStyle(showDurationPicker ? DS.Colors.accent : Color.white.opacity(0.1))
                    }
                }
                .buttonStyle(.plain)
                .focused($activeField, equals: .targetTime)
                
                // Expandable Duration Picker Wheels
                if showDurationPicker {
                    VStack(spacing: 8) {
                        HStack {
                            Text("Select Duration (HH : MM : SS)")
                                .font(.system(size: 10, weight: .bold))
                                .foregroundStyle(DS.Colors.outline)
                                .tracking(1.0)
                            Spacer()
                            Button("Done") {
                                withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) {
                                    showDurationPicker = false
                                    activeField = nil
                                }
                                Task { await saveProfile() }
                            }
                            .font(.system(size: 11, weight: .bold))
                            .foregroundStyle(DS.Colors.accent)
                        }
                        .padding(.horizontal, 12)
                        .padding(.top, 8)
                        
                        HStack(spacing: 0) {
                            Picker("Hours", selection: $targetHours) {
                                ForEach(0..<24) { h in
                                    Text("\(h) h").tag(h)
                                }
                            }
                            .pickerStyle(.wheel)
                            
                            Picker("Minutes", selection: $targetMinutes) {
                                ForEach(0..<60) { m in
                                    Text("\(m) m").tag(m)
                                }
                            }
                            .pickerStyle(.wheel)
                            
                            Picker("Seconds", selection: $targetSeconds) {
                                ForEach(0..<60) { s in
                                    Text("\(s) s").tag(s)
                                }
                            }
                            .pickerStyle(.wheel)
                        }
                        .frame(height: 110)
                    }
                    .background(Color.white.opacity(0.02))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12)
                            .stroke(Color.white.opacity(0.08), lineWidth: 1)
                    )
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .padding(.top, 4)
                }
            }
        }
    }
    
    // WEEKLY CONSTRAINTS schedule matrix
    private var weeklyConstraintsSection: some View {
        GlassPanelCard {
            VStack(alignment: .leading, spacing: 20) {
                // Section Header
                HStack(spacing: 8) {
                    Image(systemName: "square.grid.3x3.fill")
                        .font(.system(size: 15))
                        .foregroundStyle(DS.Colors.outline)
                    Text("WEEKLY CONSTRAINTS")
                        .font(.system(size: 11, weight: .bold))
                        .tracking(1.5)
                        .foregroundStyle(.white)
                }
                .padding(.bottom, 2)
                
                // M, T, W, T, F, S, S header labels
                HStack {
                    // Margin matching the sport rows header
                    Text("")
                        .frame(width: 55, alignment: .leading)
                    
                    Spacer()
                    
                    HStack(spacing: 4) {
                        ForEach(dayLabels, id: \.self) { label in
                            Text(String(label.prefix(1))) // M, T, W, T, F, S, S
                                .font(.system(size: 10, weight: .bold))
                                .tracking(1.0)
                                .foregroundStyle(DS.Colors.outline)
                                .frame(width: 28, alignment: .center)
                        }
                    }
                }
                
                // Sport Matrix Rows
                VStack(spacing: 14) {
                    sportMatrixRow(title: "SWIM", sport: "swim", selection: $selectedSwimDays)
                        .onChange(of: selectedSwimDays) { _ in Task { await saveProfile() } }
                    sportMatrixRow(title: "BIKE", sport: "bike", selection: $selectedBikeDays)
                        .onChange(of: selectedBikeDays) { _ in Task { await saveProfile() } }
                    sportMatrixRow(title: "RUN", sport: "run", selection: $selectedRunDays)
                        .onChange(of: selectedRunDays) { _ in Task { await saveProfile() } }
                    sportMatrixRow(title: "STRNG", sport: "strength", selection: $selectedStrengthDays)
                        .onChange(of: selectedStrengthDays) { _ in Task { await saveProfile() } }
                }
            }
        }
    }
    
    private func sportMatrixRow(title: String, sport: String, selection: Binding<Set<String>>) -> some View {
        HStack {
            Text(title)
                .font(.system(size: 10, weight: .bold))
                .tracking(1.5)
                .foregroundStyle(DS.Colors.outline)
                .frame(width: 55, alignment: .leading)
            
            Spacer()
            
            HStack(spacing: 4) {
                ForEach(0..<daysOfWeek.count, id: \.self) { idx in
                    let day = daysOfWeek[idx]
                    let isSelected = selection.wrappedValue.contains(day)
                    
                    MatrixToggleButton(isSelected: isSelected, sport: sport) {
                        if isSelected {
                            selection.wrappedValue.remove(day)
                        } else {
                            selection.wrappedValue.insert(day)
                        }
                    }
                }
            }
        }
    }
    
    // MARK: - Notifications Section
    
    private var notificationsSection: some View {
        VStack(spacing: 0) {
            // Section Header
            HStack {
                Image(systemName: "bell.badge.fill")
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(DS.Colors.accent)
                Text("NOTIFICATIONS")
                    .font(.system(size: 12, weight: .heavy))
                    .tracking(2.0)
                    .foregroundStyle(DS.Colors.onSurface)
                Spacer()
            }
            .padding(.horizontal, 20)
            .padding(.top, 24)
            .padding(.bottom, 16)
            
            VStack(spacing: 16) {
                Toggle("Morning Readiness", isOn: $notifyMorningReadiness)
                    .tint(DS.Colors.accent)
                Toggle("Coach Analysis Ready", isOn: $notifyCoachAnalysis)
                    .tint(DS.Colors.accent)
                Toggle("Training Load Alerts", isOn: $notifyLoadAlerts)
                    .tint(DS.Colors.accent)
                Toggle("Race Countdown", isOn: $notifyRaceCountdown)
                    .tint(DS.Colors.accent)
            }
            .font(.system(size: 14, weight: .medium))
            .foregroundStyle(DS.Colors.primaryText)
            .padding(20)
            .frame(maxWidth: .infinity)
            .glassCard()
        }
    }
    
    // MARK: - Core Logic & Data Persistence
    
    private func loadProfile() async {
        isLoading = true
        errorMessage = nil
        do {
            let prof = try await NetworkManager.shared.fetchAthleteProfile()
            await MainActor.run {
                self.profile = prof
                
                // Parse date
                if let rDateStr = prof.raceDate {
                    let formatter = DateFormatter()
                    formatter.dateFormat = "yyyy-MM-dd"
                    if let d = formatter.date(from: rDateStr) {
                        self.raceDateVal = d
                        self.hasRaceDate = true
                    } else {
                        self.hasRaceDate = false
                    }
                } else {
                    self.hasRaceDate = false
                }
                
                // Parse training start date
                if let tDateStr = prof.trainingStartDate {
                    let formatter = DateFormatter()
                    formatter.dateFormat = "yyyy-MM-dd"
                    if let d = formatter.date(from: tDateStr) {
                        self.trainingStartDateVal = d
                        self.hasTrainingStartDate = true
                    } else {
                        self.hasTrainingStartDate = false
                    }
                } else {
                    self.hasTrainingStartDate = false
                }
                
                // Parse days
                self.selectedSwimDays = parseDays(prof.swimDays)
                self.selectedBikeDays = parseDays(prof.bikeDays)
                self.selectedRunDays = parseDays(prof.runDays)
                self.selectedStrengthDays = parseDays(prof.strengthDays)
                
                // Parse target time
                self.parseTargetDuration(prof.targetFinishTime)
                
                isLoading = false
            }
        } catch {
            await MainActor.run {
                errorMessage = "Failed to load athlete profile: \(error.localizedDescription)"
                isLoading = false
            }
        }
    }
    
    private func saveProfile() async {
        isSaving = true
        errorMessage = nil
        
        // Prepare days
        profile.swimDays = serializeDays(selectedSwimDays)
        profile.bikeDays = serializeDays(selectedBikeDays)
        profile.runDays = serializeDays(selectedRunDays)
        profile.strengthDays = serializeDays(selectedStrengthDays)
        
        // Prepare date
        if hasRaceDate {
            let formatter = DateFormatter()
            formatter.dateFormat = "yyyy-MM-dd"
            profile.raceDate = formatter.string(from: raceDateVal)
        } else {
            profile.raceDate = nil
        }
        
        // Prepare training start date
        if hasTrainingStartDate {
            let formatter = DateFormatter()
            formatter.dateFormat = "yyyy-MM-dd"
            profile.trainingStartDate = formatter.string(from: trainingStartDateVal)
        } else {
            profile.trainingStartDate = nil
        }
        
        // Prepare target finish time
        let hrsStr = String(format: "%02d", targetHours)
        let minsStr = String(format: "%02d", targetMinutes)
        let secsStr = String(format: "%02d", targetSeconds)
        profile.targetFinishTime = "\(hrsStr):\(minsStr):\(secsStr)"
        
        do {
            try await NetworkManager.shared.updateAthleteProfile(profile)
            await MainActor.run {
                isSaving = false
                let haptic = UINotificationFeedbackGenerator()
                haptic.notificationOccurred(.success)
                withAnimation(.spring) {
                    saveSuccess = true
                }
            }
        } catch {
            await MainActor.run {
                isSaving = false
                let haptic = UINotificationFeedbackGenerator()
                haptic.notificationOccurred(.error)
                errorMessage = "Failed to update profile: \(error.localizedDescription)"
            }
        }
    }
    
    private func parseDays(_ daysString: String?) -> Set<String> {
        guard let s = daysString, !s.isEmpty else { return [] }
        return Set(s.lowercased().split(separator: ",").map(String.init))
    }
    
    private func serializeDays(_ set: Set<String>) -> String {
        daysOfWeek.filter { set.contains($0) }.joined(separator: ",")
    }
    
    private func parseTargetDuration(_ timeStr: String?) {
        guard let s = timeStr, !s.isEmpty else {
            self.targetHours = 3
            self.targetMinutes = 45
            self.targetSeconds = 0
            return
        }
        
        let parts = s.split(separator: ":").map(String.init)
        if parts.count >= 2, let h = Int(parts[0]), let m = Int(parts[1]) {
            self.targetHours = h
            self.targetMinutes = m
            if parts.count >= 3, let sec = Int(parts[2]) {
                self.targetSeconds = sec
            } else {
                self.targetSeconds = 0
            }
        } else {
            self.targetHours = 3
            self.targetMinutes = 45
            self.targetSeconds = 0
        }
    }
    
    // MARK: - String-Numerical Bindings Helpers
    
    private func intBinding(_ value: Binding<Int?>) -> Binding<String> {
        Binding(
            get: {
                if let val = value.wrappedValue {
                    return "\(val)"
                }
                return ""
            },
            set: {
                if let parsed = Int($0) {
                    value.wrappedValue = parsed
                } else if $0.isEmpty {
                    value.wrappedValue = nil
                }
            }
        )
    }
    
    private func doubleBinding(_ value: Binding<Double?>) -> Binding<String> {
        Binding(
            get: {
                if let val = value.wrappedValue {
                    return String(format: "%.1f", val)
                }
                return ""
            },
            set: {
                if let parsed = Double($0) {
                    value.wrappedValue = parsed
                } else if $0.isEmpty {
                    value.wrappedValue = nil
                }
            }
        )
    }
}

// MARK: - Reusable Custom Styling Components



// Subtle Focus-Aware Underlined Text Field
struct SubtleUnderlineField: View {
    let label: String
    @Binding var text: String
    var placeholder: String = ""
    var isFocused: Bool = false
    var keyboardType: UIKeyboardType = .default
    
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(isFocused ? DS.Colors.accent : DS.Colors.outline)
                .tracking(1.5)
                .textCase(.uppercase)
            
            TextField(placeholder, text: $text)
                .font(.system(size: 18, weight: .light))
                .foregroundStyle(.white)
                .tint(DS.Colors.accent)
                .keyboardType(keyboardType)
                .autocorrectionDisabled()
            
            Rectangle()
                .frame(height: 1)
                .foregroundStyle(isFocused ? DS.Colors.accent : Color.white.opacity(0.1))
        }
    }
}

// Elegant Dropdown Choice Menu Field
struct ElegantDropdownField: View {
    let label: String
    @Binding var selection: String
    let options: [String]
    
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(DS.Colors.outline)
                .tracking(1.5)
                .textCase(.uppercase)
            
            Menu {
                ForEach(options, id: \.self) { opt in
                    Button(opt) {
                        selection = opt
                    }
                }
            } label: {
                HStack {
                    Text(selection.isEmpty ? "Select option" : selection)
                        .font(.system(size: 18, weight: .light))
                        .foregroundStyle(.white)
                    Spacer()
                    Image(systemName: "chevron.down")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(DS.Colors.outline)
                }
                .padding(.vertical, 4)
            }
            
            Rectangle()
                .frame(height: 1)
                .foregroundStyle(Color.white.opacity(0.1))
        }
    }
}

// Pill Segmented Selector for Sport Types
struct SegmentedSelector: View {
    @Binding var selectedType: String
    let options = ["Running", "Triathlon", "Cycling"]
    let displayNames = ["RUN", "TRIATHLON", "CYCLE"]
    
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Type")
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(DS.Colors.outline)
                .tracking(1.5)
                .textCase(.uppercase)
            
            HStack(spacing: 4) {
                ForEach(0..<options.count, id: \.self) { idx in
                    let opt = options[idx]
                    let name = displayNames[idx]
                    let isSelected = selectedType == opt
                    
                    Button {
                        withAnimation(.spring(response: 0.25, dampingFraction: 0.75)) {
                            selectedType = opt
                        }
                    } label: {
                        Text(name)
                            .font(.system(size: 10, weight: .bold))
                            .tracking(1.5)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 8)
                            .background(
                                RoundedRectangle(cornerRadius: 6)
                                    .fill(isSelected ? DS.Colors.accent : Color.clear)
                            )
                            .foregroundStyle(isSelected ? Color.black : DS.Colors.onSurface)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(4)
            .background(Color.white.opacity(0.04))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .strokeBorder(Color.white.opacity(0.06), lineWidth: 1)
            )
        }
    }
}

// Sport-Specific Availability Grid Button
struct MatrixToggleButton: View {
    let isSelected: Bool
    let sport: String
    let action: () -> Void
    
    var body: some View {
        Button(action: action) {
            ZStack {
                Circle()
                    .fill(isSelected ? activeBgColor : Color.white.opacity(0.04))
                    .frame(width: 28, height: 28)
                
                Circle()
                    .strokeBorder(isSelected ? activeStrokeColor : Color.white.opacity(0.08), lineWidth: 1)
                    .frame(width: 28, height: 28)
            }
            .frame(width: 28, height: 28)
            .contentShape(Rectangle())
            .shadow(color: isSelected ? activeShadowColor : Color.clear, radius: isSelected ? 8 : 0)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(sport) \(isSelected ? "enabled" : "disabled")")
    }
    
    private var activeBgColor: Color {
        switch sport.lowercased() {
        case "swim": return DS.Colors.success.opacity(0.15)
        case "bike": return Color(hex: "#60a5fa").opacity(0.15)
        case "run": return Color(hex: "#fb923c").opacity(0.15)
        case "strength", "strng": return Color(hex: "#a78bfa").opacity(0.15)
        default: return DS.Colors.accent.opacity(0.15)
        }
    }
    
    private var activeStrokeColor: Color {
        switch sport.lowercased() {
        case "swim": return DS.Colors.success.opacity(0.7)
        case "bike": return Color(hex: "#60a5fa").opacity(0.7)
        case "run": return Color(hex: "#fb923c").opacity(0.7)
        case "strength", "strng": return Color(hex: "#a78bfa").opacity(0.7)
        default: return DS.Colors.accent.opacity(0.7)
        }
    }
    
    private var activeShadowColor: Color {
        switch sport.lowercased() {
        case "swim": return DS.Colors.success.opacity(0.3)
        case "bike": return Color(hex: "#60a5fa").opacity(0.3)
        case "run": return Color(hex: "#fb923c").opacity(0.3)
        case "strength", "strng": return Color(hex: "#a78bfa").opacity(0.3)
        default: return DS.Colors.accent.opacity(0.3)
        }
    }
}



#Preview {
    ProfileView()
        .preferredColorScheme(.dark)
}
