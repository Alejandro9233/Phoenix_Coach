import SwiftUI

struct ProfileView: View {
    @ObservedObject private var network = NetworkManager.shared
    @State private var backendURLText = ""
    
    @State private var profile: AthleteProfile = AthleteProfile(
        name: "", age: 30, weightKg: 75.0,
        raceName: "", raceType: "Triathlon", raceDistance: "70.3", raceDate: nil,
        swimDays: "wed,sat,sun",
        bikeDays: "mon,tue,wed,thu,fri,sat,sun",
        runDays: "mon,tue,wed,thu,fri,sat,sun",
        strengthDays: "mon,wed,fri"
    )
    @State private var raceDateVal = Date()
    @State private var hasRaceDate = false
    @State private var trainingStartDateVal = Date()
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
                Color(hex: "#131315")
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
                                .foregroundStyle(Color(hex: "#c7c6ca"))
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.top, 8)
                        
                        if isLoading {
                            VStack(spacing: 16) {
                                ProgressView()
                                    .tint(Color(hex: "#ffb59a"))
                                Text("Loading athlete profile...")
                                    .font(.system(size: 14, weight: .light))
                                    .foregroundStyle(Color(hex: "#919094"))
                            }
                            .padding(80)
                            .frame(maxWidth: .infinity)
                        } else {
                            // Backend URL Configuration
                            backendConfigSection
                            
                            // General Profile Telemetry Card
                            generalTelemetrySection
                            
                            // Race Objectives Board Card
                            raceObjectivesSection
                            
                            // Weekly Constraints Availability Matrix Card
                            weeklyConstraintsSection
                            
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
            .toolbarBackground(Color(hex: "#131315"), for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    HStack(spacing: 12) {
                        // Saving Status Indicator
                        if isSaving {
                            ProgressView()
                                .tint(Color(hex: "#ffb59a"))
                                .scaleEffect(0.7)
                        } else if saveSuccess {
                            Image(systemName: "checkmark.circle.fill")
                                .font(.system(size: 14))
                                .foregroundStyle(Color(hex: "#4ade80"))
                                .onAppear {
                                    DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                                        saveSuccess = false
                                    }
                                }
                        }
                        
                        // Connection Status Router Widget
                        Button {
                            Task {
                                await network.checkConnection()
                            }
                        } label: {
                            Image(systemName: "wifi.router.fill")
                                .font(.system(size: 14))
                                .foregroundStyle(network.isConnected ? Color(hex: "#4ade80") : Color(hex: "#ffb4ab"))
                                .padding(8)
                                .background(Color.white.opacity(0.04))
                                .clipShape(Circle())
                                .overlay(
                                    Circle()
                                        .stroke(Color.white.opacity(0.1), lineWidth: 1)
                                )
                        }
                        .buttonStyle(.plain)
                    }
                }
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
    
    // BACKEND CONFIGURATION
    private var backendConfigSection: some View {
        GlassPanelCard {
            VStack(alignment: .leading, spacing: 18) {
                // Section Header
                HStack(spacing: 8) {
                    Image(systemName: "tune")
                        .font(.system(size: 15))
                        .foregroundStyle(Color(hex: "#919094"))
                    Text("BACKEND CONFIGURATION")
                        .font(.system(size: 11, weight: .bold))
                        .tracking(1.5)
                        .foregroundStyle(.white)
                }
                .padding(.bottom, 2)
                
                // Server URL Subtle Input
                SubtleUnderlineField(
                    label: "Server URL",
                    text: $backendURLText,
                    placeholder: "http://192.168.x.x:8001",
                    isFocused: activeField == .serverURL,
                    keyboardType: .URL
                )
                .focused($activeField, equals: .serverURL)
                .onSubmit {
                    network.baseURL = backendURLText
                    Task {
                        await network.checkConnection()
                    }
                }
                
                // Status Info Row
                HStack {
                    Text("Status")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(Color(hex: "#919094"))
                    Spacer()
                    HStack(spacing: 6) {
                        Circle()
                            .fill(network.isConnected ? Color(hex: "#4ade80") : Color(hex: "#ffb4ab"))
                            .frame(width: 8, height: 8)
                        Text(network.isConnected ? "Connected" : "Disconnected")
                            .font(.system(size: 12, weight: .bold))
                            .foregroundStyle(network.isConnected ? Color(hex: "#4ade80") : Color(hex: "#ffb4ab"))
                    }
                }
                .padding(.top, 2)
                
                if network.isConnected {
                    HStack {
                        Text("Ollama LLM")
                            .font(.system(size: 13, weight: .medium))
                            .foregroundStyle(Color(hex: "#919094"))
                        Spacer()
                        Text(network.isOllamaConnected ? "Available" : "Offline")
                            .font(.system(size: 12, weight: .bold))
                            .foregroundStyle(network.isOllamaConnected ? Color(hex: "#4ade80") : Color(hex: "#fb923c"))
                    }
                }
                
                Divider()
                    .background(Color.white.opacity(0.06))
                    .padding(.vertical, 2)
                
                // Actions Buttons (Test & Apply, Reset Default)
                HStack(spacing: 12) {
                    Button {
                        network.baseURL = backendURLText
                        Task {
                            await network.checkConnection()
                            if network.isConnected {
                                await loadProfile()
                            }
                        }
                    } label: {
                        Text("TEST & APPLY")
                            .font(.system(size: 10, weight: .bold))
                            .tracking(1.0)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 10)
                            .background(Color(hex: "#ffb59a").opacity(0.12))
                            .foregroundStyle(Color(hex: "#ffb59a"))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                            .overlay(
                                RoundedRectangle(cornerRadius: 8)
                                    .strokeBorder(Color(hex: "#ffb59a").opacity(0.25), lineWidth: 1)
                            )
                    }
                    .buttonStyle(.plain)
                    
                    Button {
                        network.resetToDefaultURL()
                        backendURLText = network.baseURL
                        Task {
                            if network.isConnected {
                                await loadProfile()
                            }
                        }
                    } label: {
                        Text("RESET DEFAULT")
                            .font(.system(size: 10, weight: .bold))
                            .tracking(1.0)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 10)
                            .background(Color.white.opacity(0.04))
                            .foregroundStyle(Color(hex: "#c7c6ca"))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                            .overlay(
                                RoundedRectangle(cornerRadius: 8)
                                    .strokeBorder(Color.white.opacity(0.08), lineWidth: 1)
                            )
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
    
    // GENERAL PROFILE TELEMETRY
    private var generalTelemetrySection: some View {
        GlassPanelCard {
            VStack(alignment: .leading, spacing: 20) {
                // Section Header
                HStack(spacing: 8) {
                    Image(systemName: "slider.horizontal.3")
                        .font(.system(size: 15))
                        .foregroundStyle(Color(hex: "#919094"))
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
                                .foregroundStyle(showAgePicker ? Color(hex: "#ffb59a") : Color(hex: "#919094"))
                                .tracking(1.5)
                                .textCase(.uppercase)
                            
                            HStack {
                                Text(profile.age != nil ? "\(profile.age!) yrs" : "Select Age")
                                    .font(.system(size: 17, weight: .light))
                                    .foregroundStyle(.white)
                                Spacer()
                                Image(systemName: "chevron.down")
                                    .font(.system(size: 12, weight: .semibold))
                                    .foregroundStyle(showAgePicker ? Color(hex: "#ffb59a") : Color(hex: "#919094"))
                            }
                            .padding(.vertical, 4)
                            
                            Rectangle()
                                .frame(height: 1)
                                .foregroundStyle(showAgePicker ? Color(hex: "#ffb59a") : Color.white.opacity(0.1))
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
                                .foregroundStyle(showWeightPicker ? Color(hex: "#ffb59a") : Color(hex: "#919094"))
                                .tracking(1.5)
                                .textCase(.uppercase)
                            
                            HStack {
                                Text(profile.weightKg != nil ? String(format: "%.1f kg", profile.weightKg!) : "Select Weight")
                                    .font(.system(size: 17, weight: .light))
                                    .foregroundStyle(.white)
                                Spacer()
                                Image(systemName: "chevron.down")
                                    .font(.system(size: 12, weight: .semibold))
                                    .foregroundStyle(showWeightPicker ? Color(hex: "#ffb59a") : Color(hex: "#919094"))
                            }
                            .padding(.vertical, 4)
                            
                            Rectangle()
                                .frame(height: 1)
                                .foregroundStyle(showWeightPicker ? Color(hex: "#ffb59a") : Color.white.opacity(0.1))
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
                                .foregroundStyle(Color(hex: "#919094"))
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
                            .foregroundStyle(Color(hex: "#ffb59a"))
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
                                .foregroundStyle(Color(hex: "#919094"))
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
                            .foregroundStyle(Color(hex: "#ffb59a"))
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
                            .frame(maxWidth: .infinity)
                            
                            Text(".")
                                .font(.system(size: 24, weight: .bold))
                                .foregroundStyle(.white)
                            
                            Picker("Decimal", selection: $tempWeightDec) {
                                ForEach(0..<10) { d in
                                    Text("\(d)").tag(d)
                                }
                            }
                            .pickerStyle(.wheel)
                            .frame(maxWidth: .infinity)
                            
                            Text("kg")
                                .font(.system(size: 16, weight: .medium))
                                .foregroundStyle(Color(hex: "#919094"))
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
                
                // Training Start Date
                VStack(alignment: .leading, spacing: 6) {
                    Text("Training Start Date")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(activeField == .trainingStartDate ? Color(hex: "#ffb59a") : Color(hex: "#919094"))
                        .tracking(1.5)
                        .textCase(.uppercase)
                    
                    HStack {
                        DatePicker("", selection: $trainingStartDateVal, displayedComponents: .date)
                            .labelsHidden()
                            .datePickerStyle(.compact)
                            .tint(Color(hex: "#ffb59a"))
                        Spacer()
                    }
                    .padding(.vertical, 2)
                    .onAppear {
                        hasTrainingStartDate = true
                    }
                    
                    Rectangle()
                        .frame(height: 1)
                        .foregroundStyle(activeField == .trainingStartDate ? Color(hex: "#ffb59a") : Color.white.opacity(0.1))
                }
                .focused($activeField, equals: .trainingStartDate)
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
                        .foregroundStyle(Color(hex: "#919094"))
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
                
                // Race Date & Target Time Columns
                HStack(spacing: 24) {
                    // Race Date Picker
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Race Date")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundStyle(activeField == .raceDate ? Color(hex: "#ffb59a") : Color(hex: "#919094"))
                            .tracking(1.5)
                            .textCase(.uppercase)
                        
                        HStack {
                            DatePicker("", selection: $raceDateVal, displayedComponents: .date)
                                .labelsHidden()
                                .datePickerStyle(.compact)
                                .tint(Color(hex: "#ffb59a"))
                                .onChange(of: raceDateVal) { _ in
                                    Task { await saveProfile() }
                                }
                            Spacer()
                        }
                        .padding(.vertical, 2)
                        .onAppear {
                            hasRaceDate = true
                        }
                        
                        Rectangle()
                            .frame(height: 1)
                            .foregroundStyle(activeField == .raceDate ? Color(hex: "#ffb59a") : Color.white.opacity(0.1))
                    }
                    .focused($activeField, equals: .raceDate)
                    
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
                                .foregroundStyle(showDurationPicker ? Color(hex: "#ffb59a") : Color(hex: "#919094"))
                                .tracking(1.5)
                                .textCase(.uppercase)
                            
                            HStack {
                                Text(String(format: "%02dh %02dm %02ds", targetHours, targetMinutes, targetSeconds))
                                    .font(.system(size: 17, weight: .light))
                                    .foregroundStyle(.white)
                                Spacer()
                                Image(systemName: "chevron.down")
                                    .font(.system(size: 12, weight: .semibold))
                                    .foregroundStyle(showDurationPicker ? Color(hex: "#ffb59a") : Color(hex: "#919094"))
                            }
                            .padding(.vertical, 4)
                            
                            Rectangle()
                                .frame(height: 1)
                                .foregroundStyle(showDurationPicker ? Color(hex: "#ffb59a") : Color.white.opacity(0.1))
                        }
                    }
                    .buttonStyle(.plain)
                    .focused($activeField, equals: .targetTime)
                }
                
                // Expandable Duration Picker Wheels
                if showDurationPicker {
                    VStack(spacing: 8) {
                        HStack {
                            Text("Select Duration (HH : MM : SS)")
                                .font(.system(size: 10, weight: .bold))
                                .foregroundStyle(Color(hex: "#919094"))
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
                            .foregroundStyle(Color(hex: "#ffb59a"))
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
                        .foregroundStyle(Color(hex: "#919094"))
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
                    
                    HStack(spacing: 8) {
                        ForEach(dayLabels, id: \.self) { label in
                            Text(String(label.prefix(1))) // M, T, W, T, F, S, S
                                .font(.system(size: 10, weight: .bold))
                                .tracking(1.0)
                                .foregroundStyle(Color(hex: "#919094"))
                                .frame(width: 32, alignment: .center)
                        }
                    }
                }
                
                // Sport Matrix Rows
                VStack(spacing: 14) {
                    sportMatrixRow(title: "SWIM", sport: "swim", selection: $selectedSwimDays)
                    sportMatrixRow(title: "BIKE", sport: "bike", selection: $selectedBikeDays)
                    sportMatrixRow(title: "RUN", sport: "run", selection: $selectedRunDays)
                    sportMatrixRow(title: "STRNG", sport: "strength", selection: $selectedStrengthDays)
                }
            }
        }
    }
    
    private func sportMatrixRow(title: String, sport: String, selection: Binding<Set<String>>) -> some View {
        HStack {
            Text(title)
                .font(.system(size: 10, weight: .bold))
                .tracking(1.5)
                .foregroundStyle(Color(hex: "#919094"))
                .frame(width: 55, alignment: .leading)
            
            Spacer()
            
            HStack(spacing: 8) {
                ForEach(0..<daysOfWeek.count, id: \.self) { idx in
                    let day = daysOfWeek[idx]
                    let isSelected = selection.wrappedValue.contains(day)
                    
                    MatrixToggleButton(isSelected: isSelected, sport: sport) {
                        if isSelected {
                            selection.wrappedValue.remove(day)
                        } else {
                            selection.wrappedValue.insert(day)
                        }
                        Task { await saveProfile() }
                    }
                }
            }
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
                saveSuccess = true
            }
        } catch {
            await MainActor.run {
                isSaving = false
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

// Glassmorphism Card Wrapper
struct GlassPanelCard<Content: View>: View {
    var content: Content
    
    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }
    
    var body: some View {
        content
            .padding(20)
            .background(
                RoundedRectangle(cornerRadius: 16)
                    .fill(Color.white.opacity(0.015))
            )
            .background(
                RoundedRectangle(cornerRadius: 16)
                    .strokeBorder(
                        LinearGradient(
                            gradient: Gradient(colors: [
                                Color.white.opacity(0.18),
                                Color.white.opacity(0.04)
                            ]),
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        ),
                        lineWidth: 1
                    )
            )
            .clipShape(RoundedRectangle(cornerRadius: 16))
            .shadow(color: Color.black.opacity(0.4), radius: 20, x: 0, y: 10)
    }
}

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
                .foregroundStyle(isFocused ? Color(hex: "#ffb59a") : Color(hex: "#919094"))
                .tracking(1.5)
                .textCase(.uppercase)
            
            TextField(placeholder, text: $text)
                .font(.system(size: 18, weight: .light))
                .foregroundStyle(.white)
                .tint(Color(hex: "#ffb59a"))
                .keyboardType(keyboardType)
                .autocorrectionDisabled()
            
            Rectangle()
                .frame(height: 1)
                .foregroundStyle(isFocused ? Color(hex: "#ffb59a") : Color.white.opacity(0.1))
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
                .foregroundStyle(Color(hex: "#919094"))
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
                        .foregroundStyle(Color(hex: "#919094"))
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
                .foregroundStyle(Color(hex: "#919094"))
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
                                    .fill(isSelected ? Color(hex: "#ffb59a") : Color.clear)
                            )
                            .foregroundStyle(isSelected ? Color(hex: "#380d00") : Color(hex: "#c7c6ca"))
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
                    .frame(width: 32, height: 32)
                
                Circle()
                    .strokeBorder(isSelected ? activeStrokeColor : Color.white.opacity(0.08), lineWidth: 1)
                    .frame(width: 32, height: 32)
            }
            .shadow(color: isSelected ? activeShadowColor : Color.clear, radius: isSelected ? 8 : 0)
        }
        .buttonStyle(.plain)
    }
    
    private var activeBgColor: Color {
        switch sport.lowercased() {
        case "swim": return Color(hex: "#4ade80").opacity(0.15)
        case "bike": return Color(hex: "#60a5fa").opacity(0.15)
        case "run": return Color(hex: "#fb923c").opacity(0.15)
        case "strength", "strng": return Color(hex: "#a78bfa").opacity(0.15)
        default: return Color(hex: "#ffb59a").opacity(0.15)
        }
    }
    
    private var activeStrokeColor: Color {
        switch sport.lowercased() {
        case "swim": return Color(hex: "#4ade80").opacity(0.7)
        case "bike": return Color(hex: "#60a5fa").opacity(0.7)
        case "run": return Color(hex: "#fb923c").opacity(0.7)
        case "strength", "strng": return Color(hex: "#a78bfa").opacity(0.7)
        default: return Color(hex: "#ffb59a").opacity(0.7)
        }
    }
    
    private var activeShadowColor: Color {
        switch sport.lowercased() {
        case "swim": return Color(hex: "#4ade80").opacity(0.3)
        case "bike": return Color(hex: "#60a5fa").opacity(0.3)
        case "run": return Color(hex: "#fb923c").opacity(0.3)
        case "strength", "strng": return Color(hex: "#a78bfa").opacity(0.3)
        default: return Color(hex: "#ffb59a").opacity(0.3)
        }
    }
}

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

#Preview {
    ProfileView()
        .preferredColorScheme(.dark)
}
