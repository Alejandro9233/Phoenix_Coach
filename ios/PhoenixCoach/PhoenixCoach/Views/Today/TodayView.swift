import SwiftUI
import Charts

extension String: @retroactive Identifiable {
    public var id: String { self }
}

struct TodayView: View {
    @StateObject private var network = NetworkManager.shared
    @State private var weeklyPlan: WeeklyPlanResponse?
    @State private var planStatus: WeeklyPlanStatusResponse?
    @State private var dashboard: DashboardResponse?
    @State private var refreshResponse: SmartRefreshResponse?
    
    @State private var isLoading = false
    @State private var isSyncing = false
    @State private var syncMessage = ""
    /// Start of the in-flight refresh, for the pill's elapsed-time counter.
    @State private var syncStartedAt: Date?

    // MARK: Two-tier pull
    //
    // Apple's `.refreshable` owns the gesture, the rubber-banding and the
    // spinner — hand-rolled scroll physics is what makes custom pull-to-refresh
    // feel cheap. What `.refreshable` does NOT do is wait for release: its
    // closure fires the moment the drag crosses the *system's* activation
    // distance, finger still down. Two designs died on that fact. Reading a
    // latch inside the closure always saw the pre-threshold state, and the
    // light tier the closure started set `isSyncing`, which made `handlePull`
    // inert for the rest of the drag — so the deep tier could never arm, no
    // matter how far the pull went. Two device tests, one cause.
    //
    // So the tiers are split by *when they are decided*:
    //   - Light: `.refreshable` fires it mid-drag, exactly as the system wants.
    //   - Deep: decided at the real release. `onScrollPhaseChange` reports the
    //     finger lifting; if the drag is past `deepPullThreshold` at that
    //     moment, the scrape runs — queued behind the same gesture's in-flight
    //     light tier rather than racing it.
    // A deep pull therefore does a cheap DB read it didn't strictly need
    // before scraping. That's ~1s ahead of a 30-60s operation; accepted.
    //
    // Instrumentation: the distance must come from `onScrollGeometryChange`.
    // A GeometryReader-preference probe (tried in a named space, then in
    // `.global`) reads a frozen number on device — this ScrollView moves its
    // content without re-running layout, so the probe fires once and never
    // again. And the offset must be measured against a *frozen* baseline
    // (offset at rest, re-anchored at idle), never against the live
    // `contentInsets.top`: `.refreshable` grows that inset when it activates,
    // which cancels the live-inset formula mid-drag — that was field
    // failure #1, and freezing the baseline is the fix.

    private enum SyncTier { case light, deep }

    /// How far past the top the drag must reach to arm the deep sync.
    /// Kept well under the native refresh control's resistance ceiling — a
    /// threshold you physically cannot drag to never fires.
    private let deepPullThreshold: CGFloat = 110
    /// Slack before disarming, so a finger resting on the boundary doesn't
    /// buzz repeatedly. Without this the haptics chatter and it feels broken.
    private let deepPullHysteresis: CGFloat = 22

    @State private var pullProgress: CGFloat = 0
    @State private var deepSyncArmed = false
    /// Which tier is running, while `isSyncing`. The light tier starts
    /// mid-drag, so `handlePull` must keep measuring through it; a scrape or
    /// the initial load makes pulls inert. `nil` when idle.
    @State private var syncTier: SyncTier?
    /// Finger on screen, per `onScrollPhaseChange`. Arming and disarming
    /// require it — the settle animation after release replays falling
    /// distances, and letting those trip the hysteresis would disarm the latch
    /// in the gap before the release decision runs.
    @State private var isDragging = false
    /// Deep sync requested at release while the light tier was still running.
    /// `endSync` consumes it.
    @State private var pendingDeepSync = false
    /// `contentOffset.y` when the list is at rest, captured on the first
    /// geometry callback and re-anchored whenever the scroll view settles to
    /// idle. Offsets are absolute, so this is what turns them into a drag
    /// distance — and it is deliberately a snapshot, immune to `.refreshable`
    /// growing the top inset mid-gesture.
    @State private var pullBaseline: CGFloat?
    @State private var lastOffsetY: CGFloat?
    /// Live drag distance, shown in the pill while tuning. Set false to hide.
    @State private var rawPull: CGFloat = 0
    private let showPullDebug = false
    @State private var errorMessage: String?
    @State private var showConnectionSettings = false
    
    @State private var showScraperError = false
    @State private var scraperErrorMessage = ""
    
    @State private var showHRVChart = false
    @State private var showRHRChart = false
    @State private var showLoadChart = false
    @State private var showAdaptationSheet = false
    @State private var preferOriginalProtocol = false
    
    // Design system colors matching Quiet Performance HTML mockup
                                                   
    /// Full English weekday name, matching the backend's plan day keys
    /// (`strftime("%A")` → "Sunday"). `en_US_POSIX` is required: a Spanish
    /// phone must not produce "domingo" and silently miss the lookup.
    private var todayDayName: String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "EEEE"
        return formatter.string(from: Date())
    }
    
    private var latestRecovery: RecoverySummary? {
        if let ref = refreshResponse {
            return ref.recovery
        }
        if let dash = dashboard, let first = dash.recovery.first {
            return RecoverySummary(
                hrvMs: first.hrvMs,
                restingHr: first.restingHr,
                loadRatio: first.loadRatio,
                loadRatioLabel: nil,
                cti: first.cti,
                ati: first.ati,
                tib: first.tib,
                fatigueState: nil,
                staminaLevel: dash.athlete?.staminaLevel
            )
        }
        return nil
    }
    
    private var todayDayPlan: DayPlan? {
        if let statusDay = planStatus?.days[todayDayName] {
            return DayPlan(
                summary: statusDay.summary,
                workouts: statusDay.workouts,
                rationale: statusDay.rationale,
                coachNote: statusDay.coachNote,
                adaptation: statusDay.adaptation,
                originalWorkouts: statusDay.originalWorkouts
            )
        }
        if let rec = weeklyPlan?.days[todayDayName] {
            return rec
        }
        return nil
    }
    
    var body: some View {
        NavigationStack {
            // One ScrollView, one .refreshable. The error and content states are
            // branched *inside* it on purpose: when each state owned its own
            // ScrollView, clearing `errorMessage` mid-refresh destroyed the very
            // view whose `.refreshable` task was running, cancelling the sync.
            ScrollView {
                // The pill sits outside the branch and outside the dimming: it
                // reports both refresh tiers and the error state, so it is the
                // one thing that must stay legible while the rest fades.
                VStack(spacing: 24) {
                    statusPill

                    if let err = errorMessage, weeklyPlan == nil, dashboard == nil {
                        errorView(err)
                            .frame(maxWidth: .infinity, minHeight: 400)
                    } else {
                        VStack(spacing: 24) {
                            VStack(spacing: 12) {
                                HStack(spacing: 12) {
                                    hrvCard
                                    rhrCard
                                }
                                loadRatioCard
                            }

                            timelineLink

                            workoutProtocolSection

                            complianceSection

                            rationaleSection
                        }
                        .opacity(isSyncing ? 0.3 : 1.0)
                    }
                }
                .padding()
            }
            .onScrollGeometryChange(for: CGFloat.self, of: { $0.contentOffset.y }) { _, offsetY in
                handlePull(offsetY: offsetY)
            }
            .refreshable {
                // Fires at the system's activation distance — mid-drag, finger
                // still down, never at release. No release state can be read
                // here, so this closure can only ever be the light tier. The
                // deep tier is decided below, when the finger actually lifts.
                print("🔄 Refresh control fired — DB refresh")
                await refreshFromDatabase()
            }
            .onScrollPhaseChange { _, newPhase in
                isDragging = newPhase == .tracking || newPhase == .interacting

                // Re-anchor zero at true rest, so a rotation or bar-height
                // change can't skew every later reading. Guarded on !isSyncing:
                // idle-with-spinner-extended is also "idle", and anchoring to it
                // would fold the spinner inset into every later distance.
                if newPhase == .idle, !isSyncing, let restY = lastOffsetY {
                    pullBaseline = restY
                }

                // The release decision. Armed can only be true if the drag
                // went past `deepPullThreshold` and stayed there.
                if !isDragging, deepSyncArmed {
                    deepSyncArmed = false
                    if isSyncing {
                        // The same gesture's light tier is still running —
                        // scrape when it finishes, don't race it.
                        print("🔄 Deep pull released — scrape queued behind light refresh")
                        pendingDeepSync = true
                    } else {
                        print("🔄 Deep pull released — COROS scrape")
                        Task { await performSmartRefresh() }
                    }
                }
            }
            .background {
                ZStack {
                    DS.Colors.background
                    RadialGradient(
                        gradient: Gradient(colors: [
                            DS.Colors.accent.opacity(0.12),
                            .clear
                        ]),
                        center: .top,
                        startRadius: 0,
                        endRadius: 400
                    )
                }
                .ignoresSafeArea()
            }
            .navigationTitle("")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        Task {
                            print("🔄 Toolbar refresh tapped")
                            await performSmartRefresh()
                        }
                    } label: {
                        if isSyncing {
                            ProgressView()
                                .tint(DS.Colors.accent)
                                .scaleEffect(0.8)
                        } else {
                            Image(systemName: "arrow.clockwise")
                                .font(.system(size: 14, weight: .semibold))
                                .foregroundStyle(DS.Colors.accent)
                        }
                    }
                    .disabled(isSyncing)
                }
            }
            .sheet(isPresented: $showHRVChart) {
                MetricChartSheet(title: "HRV (ms)", data: dashboard?.recovery ?? [], metricType: .hrv)
            }
            .sheet(isPresented: $showRHRChart) {
                MetricChartSheet(title: "Resting HR (bpm)", data: dashboard?.recovery ?? [], metricType: .rhr)
            }
            .sheet(isPresented: $showLoadChart) {
                MetricChartSheet(title: "Load Ratio", data: dashboard?.recovery ?? [], metricType: .load)
            }
            .sheet(isPresented: $showAdaptationSheet) {
                if let todayPlan = todayDayPlan {
                    AdaptationComparisonSheet(
                        todayPlan: todayPlan,
                        latestRecovery: latestRecovery,
                        preferOriginal: $preferOriginalProtocol
                    )
                }
            }
            .alert("Scraper Error", isPresented: $showScraperError) {
                Button("OK", role: .cancel) { }
            } message: {
                Text(scraperErrorMessage)
            }
            .task {
                if weeklyPlan == nil {
                    await loadInitialData()
                }
            }
            // Posted when another tab rewrites the plan — confirming an injury
            // in Coach, or editing the block calendar. Without this, Today keeps
            // showing the session that was just removed.
            .onReceive(NotificationCenter.default.publisher(for: NSNotification.Name("PlanUpdated"))) { _ in
                Task {
                    await fetchWeeklyPlan()
                    await fetchPlanStatus()
                }
            }
        }
    }
    
    // MARK: - UI Components
    
    /// What the pill says. During a drag it previews what releasing will do, so
    /// the deep tier is discovered by pulling rather than by being told.
    private var pillText: String {
        // Temporary tuning aid: the real drag distance against the threshold.
        // Checked before `isSyncing` — the light tier starts mid-drag, and the
        // whole point is to watch the number climb *through* it. Shown even at
        // zero: a readout that hides itself at 0 looks identical to a probe
        // that never fired. Flip `showPullDebug` off once the numbers are set.
        if showPullDebug, isDragging || !isSyncing {
            guard pullBaseline != nil else { return "PULL — NO BASELINE" }
            return "PULL \(Int(rawPull)) / \(Int(deepPullThreshold))\(deepSyncArmed ? " ARMED" : "")"
        }
        // Armed outranks the sync message: with the light tier already running
        // underneath, the pill's job is to say what release will do.
        if deepSyncArmed { return "RELEASE TO SCRAPE COROS" }
        if isSyncing { return syncMessage }
        if pullProgress > 0.12 { return "KEEP PULLING TO SCRAPE COROS" }
        return network.isConnected ? "Biometrics Synced" : "Connection Offline"
    }

    private var statusPill: some View {
        HStack(spacing: 8) {
            if isSyncing {
                ProgressView()
                    .controlSize(.mini)
                    .tint(DS.Colors.accent)
            } else {
                Circle()
                    .fill(deepSyncArmed ? DS.Colors.accent : (network.isConnected ? Color.green : Color.red))
                    .frame(width: 8, height: 8)
                    .scaleEffect(deepSyncArmed ? 1.4 : 1.0)
            }

            Text(pillText)
                .font(.system(size: 11, weight: .medium))
                .tracking(1.1)
                .foregroundStyle(deepSyncArmed ? DS.Colors.accent : DS.Colors.primaryText)

            // A cold Render dyno takes 30-60s. Without a ticking number the pill
            // looks frozen and indistinguishable from a hang, so count real
            // elapsed time rather than faking staged progress messages.
            if isSyncing, let started = syncStartedAt {
                TimelineView(.periodic(from: started, by: 1)) { ctx in
                    Text("\(max(0, Int(ctx.date.timeIntervalSince(started))))s")
                        .font(.system(size: 11, weight: .semibold).monospacedDigit())
                        .foregroundStyle(DS.Colors.accent)
                }
            }

            // Rotates 1:1 with the drag. Tying it to `pullProgress` rather than
            // to a threshold is what makes the pull feel attached to the finger
            // instead of snapping between two states.
            if !isSyncing {
                Image(systemName: deepSyncArmed ? "arrow.down.circle.fill" : "arrow.triangle.2.circlepath")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(DS.Colors.accent.opacity(0.7 + pullProgress * 0.3))
                    .rotationEffect(.degrees(Double(pullProgress) * 180))
                    .scaleEffect(deepSyncArmed ? 1.25 : 1.0)
            }
        }
        .animation(.easeInOut(duration: 0.2), value: isSyncing)
        // Spring, not easeInOut: arming should feel like a detent clicking over.
        .animation(.spring(response: 0.28, dampingFraction: 0.62), value: deepSyncArmed)
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(.ultraThinMaterial)
        .clipShape(Capsule())
        .overlay(
            Capsule()
                .stroke(
                    deepSyncArmed
                        ? DS.Colors.accent.opacity(0.9)
                        : Color.white.opacity(0.1 + pullProgress * 0.35),
                    lineWidth: deepSyncArmed ? 1.5 : 1
                )
        )
        .accessibilityLabel(isSyncing ? "Syncing" : (network.isConnected ? "Biometrics Synced" : "Connection Offline"))
        .accessibilityHint("Pull down to refresh, or pull further to scrape COROS")
    }

    /// Drives the pull readout and arms the deep tier.
    ///
    /// Haptics fire only on threshold *crossings*, never continuously — a buzz
    /// on every scroll event is the single fastest way to make a gesture feel
    /// broken. `deepPullHysteresis` keeps a finger hovering at the boundary from
    /// rearming over and over.
    private func handlePull(offsetY: CGFloat) {
        lastOffsetY = offsetY

        // First reading is the resting position — the list always lays out at
        // the top — so it defines zero. Captured ahead of the sync guard below:
        // the initial load holds `isSyncing` for the whole 30-60s cold start,
        // and a baseline that isn't set until that ends is a baseline taken
        // while the refresh control is still extended.
        guard let baseline = pullBaseline else {
            pullBaseline = offsetY
            return
        }

        // A pull during a scrape or the initial load is deliberately inert — a
        // second scrape queued behind the first is never what the athlete
        // meant. The *light* tier is exempt, and not by choice: `.refreshable`
        // starts it mid-drag, so going inert on it would throw away the rest
        // of the drag and make the deep threshold unreachable — field failure
        // number two. Clear the readout too, or the last drag distance is
        // still sitting there when the sync ends and the pill flashes a stale
        // number.
        guard !isSyncing || syncTier == .light else {
            if pullProgress != 0 { pullProgress = 0 }
            if rawPull != 0 { rawPull = 0 }
            return
        }

        // Dragging past the top pushes the offset *below* its resting value, so
        // rest-minus-current is the pull distance. Scrolling down the list makes
        // it negative — clamped to zero, which is what `max` is doing here.
        // While the refresh spinner's inset is extended the reading skews high
        // by about the spinner height; arming is finger-gated and a light sync
        // lasts ~1s, so the skew is tolerated rather than tracked.
        let pull = max(0, baseline - offsetY)
        rawPull = pull
        pullProgress = min(1, pull / deepPullThreshold)

        // `isDragging` gates both transitions: only the finger arms, and only
        // the finger disarms. Without it, the settle animation's falling
        // values would disarm the latch before the release decision reads it.
        if !deepSyncArmed, isDragging, pull >= deepPullThreshold {
            deepSyncArmed = true
            UIImpactFeedbackGenerator(style: .rigid).impactOccurred()
        } else if deepSyncArmed, isDragging, pull < deepPullThreshold - deepPullHysteresis {
            deepSyncArmed = false
            UIImpactFeedbackGenerator(style: .soft).impactOccurred()
        }
    }
    
    private var hrvCard: some View {
        Button(action: { if dashboard?.recovery.isEmpty == false { showHRVChart = true } }) {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Image(systemName: "heart.text.square")
                        .font(.subheadline)
                        .foregroundStyle(DS.Colors.accent)
                    Spacer()
                    Text("HRV")
                        .font(.system(size: 11, weight: .bold))
                        .tracking(1.1)
                        .foregroundStyle(DS.Colors.outline)
                }
                
                Spacer()
                
                HStack(alignment: .bottom, spacing: 2) {
                    if let hrv = latestRecovery?.hrvMs {
                        Text("\(Int(hrv))")
                            .font(.system(size: 36, weight: .ultraLight))
                            .foregroundStyle(DS.Colors.primaryText)
                        Text("ms")
                            .font(.caption2)
                            .foregroundStyle(DS.Colors.outline)
                            .padding(.bottom, 6)
                    } else {
                        Text("--")
                            .font(.system(size: 36, weight: .ultraLight))
                            .foregroundStyle(DS.Colors.outline)
                    }
                }
                
                Spacer()
                
                if let hrv = latestRecovery?.hrvMs, let baseline = dashboard?.athlete?.hrvBaseline, baseline > 0 {
                    let pctDiff = ((hrv - baseline) / baseline) * 100.0
                    let sign = pctDiff >= 0 ? "+" : ""
                    Text("\(sign)\(Int(pctDiff))% vs baseline")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(pctDiff >= -15 ? .green : .red)
                } else {
                    Text("No baseline")
                        .font(.system(size: 10, weight: .regular))
                        .foregroundStyle(DS.Colors.outline)
                }
            }
            .frame(maxWidth: .infinity, minHeight: 120)
            .glassCard()
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
    }
    
    private var rhrCard: some View {
        Button(action: { if dashboard?.recovery.isEmpty == false { showRHRChart = true } }) {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Image(systemName: "heart.fill")
                        .font(.subheadline)
                        .foregroundStyle(DS.Colors.accent)
                    Spacer()
                    Text("RHR")
                        .font(.system(size: 11, weight: .bold))
                        .tracking(1.1)
                        .foregroundStyle(DS.Colors.outline)
                }
                
                Spacer()
                
                HStack(alignment: .bottom, spacing: 2) {
                    if let rhr = latestRecovery?.restingHr {
                        Text("\(rhr)")
                            .font(.system(size: 36, weight: .ultraLight))
                            .foregroundStyle(DS.Colors.primaryText)
                        Text("bpm")
                            .font(.caption2)
                            .foregroundStyle(DS.Colors.outline)
                            .padding(.bottom, 6)
                    } else {
                        Text("--")
                            .font(.system(size: 36, weight: .ultraLight))
                            .foregroundStyle(DS.Colors.outline)
                    }
                }
                
                Spacer()
                
                if let rhr = latestRecovery?.restingHr, let baseRhr = dashboard?.athlete?.hrRest {
                    let diff = rhr - baseRhr
                    let sign = diff >= 0 ? "+" : ""
                    Text("\(sign)\(diff) bpm vs rest")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(diff <= 5 ? .green : .red)
                } else {
                    Text("No baseline")
                        .font(.system(size: 10, weight: .regular))
                        .foregroundStyle(DS.Colors.outline)
                }
            }
            .frame(maxWidth: .infinity, minHeight: 120)
            .glassCard()
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
    }
    
    private var loadRatioCard: some View {
        Button(action: { if dashboard?.recovery.isEmpty == false { showLoadChart = true } }) {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Image(systemName: "waveform.path.ecg")
                        .font(.subheadline)
                        .foregroundStyle(DS.Colors.accent)
                    
                    Text("LOAD RATIO")
                        .font(.system(size: 11, weight: .bold))
                        .tracking(1.1)
                        .foregroundStyle(DS.Colors.outline)
                    
                    Spacer()
                    
                    let label = latestRecovery?.loadRatioLabel ?? loadRatioLabel(for: latestRecovery?.loadRatio)
                    Text(label.uppercased())
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(.black)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(badgeColor(for: label))
                        .clipShape(Capsule())
                }
                
                HStack(alignment: .center, spacing: 16) {
                    HStack(alignment: .bottom, spacing: 2) {
                        if let ratio = latestRecovery?.loadRatio {
                            Text(String(format: "%.2f", ratio))
                                .font(.system(size: 36, weight: .ultraLight))
                                .foregroundStyle(DS.Colors.primaryText)
                        } else {
                            Text("--")
                                .font(.system(size: 36, weight: .ultraLight))
                                .foregroundStyle(DS.Colors.outline)
                        }
                    }
                    
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Injury risk evaluation based on acute vs chronic load")
                            .font(.caption2)
                            .foregroundStyle(DS.Colors.onSurface)
                        
                        if let cti = latestRecovery?.cti, let ati = latestRecovery?.ati {
                            Text("ATL: \(Int(ati)) • CTL: \(Int(cti))")
                                .font(.system(size: 10, weight: .medium))
                                .foregroundStyle(DS.Colors.outline)
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity)
            .glassCard()
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
    }
    
    private var timelineLink: some View {
        NavigationLink(destination: BlockCalendarView()) {
            HStack(spacing: 12) {
                Image(systemName: "chart.line.uptrend.xyaxis")
                    .font(.title3)
                    .foregroundStyle(DS.Colors.accent)
                
                VStack(alignment: .leading, spacing: 2) {
                    Text("TRAINING TIMELINE")
                        .font(.system(size: 11, weight: .semibold))
                        .tracking(1.1)
                        .foregroundStyle(DS.Colors.outline)
                    Text("View training phases and full calendar")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                
                Spacer()
                
                Image(systemName: "chevron.right")
                    .font(.footnote)
                    .foregroundStyle(DS.Colors.outline)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .glassCard()
        }
        .buttonStyle(.plain)
    }
    
    private var workoutProtocolSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("TODAY'S WORKOUT PROTOCOL")
                    .font(.system(size: 11, weight: .bold))
                    .tracking(1.1)
                    .foregroundStyle(DS.Colors.outline)
                
                Spacer()
                
                if let todayPlan = todayDayPlan,
                   todayPlan.adaptation != nil && !(todayPlan.adaptation?.isEmpty ?? true) {
                    Button {
                        showAdaptationSheet = true
                    } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "waveform.path.ecg")
                                .font(.system(size: 10, weight: .bold))
                            Text("Compare Telemetry")
                                .font(.system(size: 10, weight: .bold))
                        }
                        .foregroundStyle(DS.Colors.accent)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Color.white.opacity(0.08))
                        .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 4)
            
            if let todayPlan = todayDayPlan {
                let hasAdaptation = todayPlan.adaptation != nil && !(todayPlan.adaptation?.isEmpty ?? true)
                let originalWorkouts = todayPlan.originalWorkouts ?? []
                let activeWorkouts = todayPlan.workouts ?? []
                
                if hasAdaptation && !originalWorkouts.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 16) {
                            ProtocolCard(
                                cardTitle: "AI Adapted Protocol",
                                workouts: activeWorkouts,
                                rationale: todayPlan.rationale,
                                coachNote: todayPlan.coachNote,
                                isAdapted: true,
                                adaptationReason: todayPlan.adaptation,
                                onTapCompare: { showAdaptationSheet = true }
                            )
                            .frame(width: UIScreen.main.bounds.width - 32)
                            
                            ProtocolCard(
                                cardTitle: "Original Blueprint",
                                workouts: originalWorkouts,
                                rationale: nil,
                                coachNote: nil,
                                isAdapted: false,
                                adaptationReason: nil,
                                onTapCompare: nil
                            )
                            .frame(width: UIScreen.main.bounds.width - 32)
                        }
                        .scrollTargetLayout()
                    }
                    .scrollTargetBehavior(.paging)
                } else {
                    ProtocolCard(
                        cardTitle: "Original Protocol",
                        workouts: activeWorkouts,
                        rationale: todayPlan.rationale,
                        coachNote: todayPlan.coachNote,
                        isAdapted: false,
                        adaptationReason: nil,
                        onTapCompare: nil
                    )
                }
            } else {
                emptyDayCard
            }
        }
    }
    
    private var complianceSection: some View {
        Group {
            if let score = planStatus?.weekProgress?.complianceScore {
                VStack(alignment: .leading, spacing: 14) {
                    Text("WEEKLY ADHERENCE")
                        .font(.system(size: 11, weight: .bold))
                        .tracking(1.1)
                        .foregroundStyle(DS.Colors.outline)
                    
                    HStack(alignment: .center, spacing: 16) {
                        Text("\(score)")
                            .font(.system(size: 48, weight: .ultraLight))
                            .foregroundStyle(complianceColor(for: score))
                        
                        VStack(alignment: .leading, spacing: 4) {
                            Text("/ 100")
                                .font(.system(size: 16, weight: .semibold))
                                .foregroundStyle(DS.Colors.outline)
                            
                            if let completed = planStatus?.weekProgress?.sessionsCompleted,
                               let planned = planStatus?.weekProgress?.sessionsPlanned {
                                Text("\(completed) of \(planned) sessions completed")
                                    .font(.caption2)
                                    .foregroundStyle(DS.Colors.onSurface)
                            }
                        }
                        Spacer()
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .glassCard()
            }
        }
    }
    
    private func complianceColor(for score: Int) -> Color {
        if score >= 90 { return .green }
        if score >= 80 { return .teal }
        if score >= 70 { return .orange }
        return .red
    }
    
    private var rationaleSection: some View {
        Group {
            if let todayPlan = todayDayPlan {
                VStack(alignment: .leading, spacing: 14) {
                    Text("COACH'S RATIONALE & NOTE")
                        .font(.system(size: 11, weight: .bold))
                        .tracking(1.1)
                        .foregroundStyle(DS.Colors.outline)
                    
                    if let rationale = todayPlan.rationale, !rationale.isEmpty {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("RATIONALE")
                                .font(.system(size: 9, weight: .bold))
                                .tracking(0.9)
                                .foregroundStyle(DS.Colors.accent)
                            Text(rationale)
                                .font(.system(size: 13))
                                .foregroundStyle(DS.Colors.onSurface)
                                .lineSpacing(3)
                        }
                    }
                    
                    if let note = todayPlan.coachNote, !note.isEmpty {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("COACH NOTE")
                                .font(.system(size: 9, weight: .bold))
                                .tracking(0.9)
                                .foregroundStyle(DS.Colors.accent)
                            Text(note)
                                .font(.system(size: 13).italic())
                                .foregroundStyle(DS.Colors.onSurface)
                                .lineSpacing(3)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .glassCard()
            }
        }
    }
    
    private var emptyDayCard: some View {
        VStack(spacing: 12) {
            Image(systemName: "zzz")
                .font(.largeTitle)
                .foregroundStyle(DS.Colors.outline)
            Text("Rest Day")
                .font(.headline.bold())
                .foregroundStyle(DS.Colors.primaryText)
            Text("No structured training scheduled for today. Focus on active recovery, stretching, or general wellness.")
                .font(.caption)
                .foregroundStyle(DS.Colors.onSurface)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 24)
        .glassCard()
    }
    
    // MARK: - Logic & Network Helpers
    
    /// Both refresh tiers and the initial load drive the status pill through
    /// these, so the spinner, the message and the elapsed counter can never
    /// disagree about whether a sync is running.
    private func beginSync(_ message: String, tier: SyncTier) {
        isSyncing = true
        syncTier = tier
        syncMessage = message
        syncStartedAt = Date()
        // Drop the drag readout so the pill switches cleanly to sync state.
        pullProgress = 0
    }

    private func endSync(haptic: UIImpactFeedbackGenerator.FeedbackStyle? = nil) {
        isSyncing = false
        syncTier = nil
        syncStartedAt = nil
        if let haptic {
            UIImpactFeedbackGenerator(style: haptic).impactOccurred()
        }
        // A deep pull released while this sync was running parked its request
        // here rather than racing two syncs. Honor it now.
        if pendingDeepSync {
            pendingDeepSync = false
            Task { await performSmartRefresh() }
        }
    }

    private func loadInitialData() async {
        // .deep so pulls stay inert for the whole cold start, same as a scrape.
        beginSync("Loading training data...", tier: .deep)
        // No forceRefresh here on purpose — the first load may use the client cache.
        async let dashTask: () = fetchDashboard()
        async let planTask: () = fetchWeeklyPlan()
        async let statusTask: () = fetchPlanStatus()
        _ = await (dashTask, planTask, statusTask)
        endSync()
    }
    
    private func fetchWeeklyPlan() async {
        isLoading = true
        errorMessage = nil
        do {
            let plan = try await network.fetchWeeklyPlan()
            await MainActor.run {
                self.weeklyPlan = plan
                self.isLoading = false
            }
        } catch {
            await MainActor.run {
                self.errorMessage = error.localizedDescription
                self.isLoading = false
            }
        }
    }
    
    private func fetchPlanStatus() async {
        do {
            let status = try await network.fetchWeeklyPlanStatus()
            await MainActor.run {
                self.planStatus = status
            }
        } catch {
            print("Plan status fetch error: \(error)")
        }
    }
    
    private func fetchDashboard(forceRefresh: Bool = false) async {
        do {
            let dash = try await network.fetchDashboard(forceRefresh: forceRefresh)
            await MainActor.run {
                self.dashboard = dash
            }
        } catch {
            print("Dashboard fetch error: \(error)")
        }
    }
    
    /// Re-reads what the backend already holds in Postgres. No COROS scrape.
    ///
    /// This is the cheap tier — roughly a second — and it's what pull-to-refresh
    /// runs. `forceRefresh: true` bypasses NetworkManager's 5-minute client-side
    /// memory cache: an explicit pull should never hand back a cached copy, even
    /// though the server work is only a row read.
    private func refreshFromDatabase() async {
        guard !isSyncing else { return }
        beginSync("Refreshing data...", tier: .light)

        await detachedFetch { await fetchAllFromBackend() }

        await MainActor.run { self.endSync(haptic: .light) }
    }

    /// Runs network work outside the caller's cancellation scope.
    ///
    /// `.refreshable` runs its closure as a *structured child* of SwiftUI's
    /// refresh task and cancels it the moment the refresh control retracts.
    /// Cancellation propagates straight into `URLSession`, failing every
    /// in-flight request with `NSURLErrorCancelled` (-999) — so the pull looks
    /// like it succeeded while having fetched nothing. This is the same failure
    /// the `Task.sleep` removal exposed: killing the sleeps stopped the silent
    /// swallow, but the cancellation just moved downstream into the HTTP calls.
    ///
    /// An unstructured `Task` does not inherit cancellation, so the fetch runs
    /// to completion regardless. Awaiting `.value` on a non-throwing task has no
    /// cancellation throw point, so the caller still waits for the real result.
    /// The pill's own spinner and elapsed counter — not the refresh control —
    /// are what tell the athlete a sync is still running.
    private func detachedFetch(_ body: @escaping () async -> Void) async {
        await Task { await body() }.value
    }

    /// Starts the deep refresh as a *backend job*, polls it, then re-reads
    /// everything once. The scrape runs server-side: the phone no longer holds
    /// a minutes-long HTTP request open, and closing the app mid-scrape no
    /// longer kills it — the data is simply there on the next open.
    ///
    /// The expensive tier — 30-90s, more on a cold dyno. Reached by the
    /// release decision in `onScrollPhaseChange` (never by `.refreshable`,
    /// which fires mid-drag), by `endSync` draining `pendingDeepSync`, and by
    /// the toolbar button.
    ///
    /// Every await in here must stay inside `detachedFetch`. Under
    /// `.refreshable` this function runs as a structured child of SwiftUI's
    /// refresh task, where cancellation makes a bare `URLSession` call die
    /// with -999 and a bare `Task.sleep` throw `CancellationError` — both
    /// swallowed by catches, silently skipping the scrape. Inside
    /// `detachedFetch`'s unstructured Task no cancellation ever arrives, which
    /// is what makes the poll loop's sleep safe *there and only there*.
    private func performSmartRefresh() async {
        guard !isSyncing else { return }
        beginSync("Starting deep sync...", tier: .deep)

        await detachedFetch {
            do {
                var status = try await network.startSmartRefresh()

                // Poll until the job settles. The generous deadline is for the
                // phone's patience, not the job's — on timeout the job keeps
                // running server-side and a later refresh picks up its result.
                let deadline = Date().addingTimeInterval(300)
                while status.state == "running", Date() < deadline {
                    try await Task.sleep(for: .seconds(2))
                    status = try await network.smartRefreshStatus()
                    if !status.stage.isEmpty {
                        await MainActor.run { self.syncMessage = status.stage }
                    }
                }

                await MainActor.run {
                    switch status.state {
                    case "done":
                        if let result = status.result {
                            self.refreshResponse = result
                            if result.syncStatus == "partial" {
                                let msg = result.syncMessage
                                self.scraperErrorMessage = msg.isEmpty ? "Data could not be scraped." : msg
                                self.showScraperError = true
                            }
                        }
                    case "error":
                        self.scraperErrorMessage = status.error ?? "Deep sync failed."
                        self.showScraperError = true
                    default:
                        // Deadline hit, or the dyno restarted and lost the job.
                        self.scraperErrorMessage = "Still syncing on the server. Pull to refresh in a minute."
                        self.showScraperError = true
                    }
                }
            } catch {
                print("Smart refresh job error (non-fatal): \(error)")
                // Don't abort — still fetch latest data below
            }

            // Always re-read from the backend, whatever the job did.
            await MainActor.run { self.syncMessage = "Refreshing data..." }
            await fetchAllFromBackend()
        }

        await MainActor.run { self.endSync(haptic: .medium) }
    }

    /// The three plain reads both refresh tiers end with.
    private func fetchAllFromBackend() async {
        async let dashTask: () = fetchDashboard(forceRefresh: true)
        async let planTask: () = fetchWeeklyPlan()
        async let statusTask: () = fetchPlanStatus()
        _ = await (dashTask, planTask, statusTask)
    }
    
    private func loadRatioLabel(for ratio: Double?) -> String {
        guard let ratio = ratio else { return "UNKNOWN" }
        if ratio < 0.8 { return "DETRAINING" }
        if ratio <= 1.3 { return "OPTIMAL" }
        if ratio <= 1.5 { return "OVERREACHING" }
        return "HIGH RISK"
    }
    
    private func badgeColor(for label: String) -> Color {
        switch label.uppercased() {
        case "OPTIMAL":
            return .green
        case "DETRAINING", "OVERREACHING":
            return .orange
        case "HIGH RISK":
            return .red
        default:
            return .gray
        }
    }
    
    private func errorView(_ error: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.largeTitle)
                .foregroundStyle(DS.Colors.accent.opacity(0.8))
            Text(error)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(DS.Colors.onSurface)
                .multilineTextAlignment(.center)
                .padding(.horizontal)
            Button("Retry") {
                Task {
                    await loadInitialData()
                }
            }
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(.black)
            .padding(.horizontal, 20)
            .padding(.vertical, 8)
            .background(DS.Colors.accent.opacity(0.8))
            .clipShape(Capsule())
        }
    }
}

// MARK: - ProtocolCard Component

struct ProtocolCard: View {
    let cardTitle: String
    let workouts: [Workout]
    let rationale: String?
    let coachNote: String?
    let isAdapted: Bool
    let adaptationReason: String?
    var onTapCompare: (() -> Void)? = nil
    
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text(cardTitle.uppercased())
                    .font(.system(size: 11, weight: .bold))
                    .tracking(1.1)
                    .foregroundStyle(isAdapted ? DS.Colors.accent : DS.Colors.outline)
                
                Spacer()
                
                if isAdapted {
                    Text("OPTIMIZED")
                        .font(.system(size: 8, weight: .bold))
                        .foregroundStyle(.black)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(DS.Colors.accent)
                        .clipShape(Capsule())
                }
            }
            
            if isAdapted, let reason = adaptationReason {
                if let onTap = onTapCompare {
                    Button(action: onTap) {
                        HStack(spacing: 8) {
                            Image(systemName: "sparkles")
                                .font(.system(size: 11, weight: .bold))
                                .foregroundStyle(DS.Colors.accent)
                            
                            Text("Reason: \(reason)")
                                .font(.system(size: 11, weight: .medium))
                                .foregroundStyle(DS.Colors.accent)
                                .lineLimit(2)
                            
                            Spacer()
                            
                            HStack(spacing: 2) {
                                Text("Compare")
                                    .font(.system(size: 10, weight: .bold))
                                Image(systemName: "chevron.right")
                                    .font(.system(size: 9, weight: .bold))
                            }
                            .foregroundStyle(DS.Colors.outline)
                        }
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(DS.Colors.accent.opacity(0.08))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain)
                } else {
                    Text("Reason: \(reason)")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(DS.Colors.accent)
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(DS.Colors.accent.opacity(0.1))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                }
            }
            
            if workouts.isEmpty {
                Text("No activities planned today.")
                    .font(.subheadline)
                    .foregroundStyle(DS.Colors.onSurface)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.vertical, 20)
            } else {
                ForEach(workouts, id: \.title) { workout in
                    VStack(alignment: .leading, spacing: 12) {
                        HStack(spacing: 8) {
                            Image(systemName: workout.sportIcon)
                                .font(.system(size: 15, weight: .semibold))
                                .foregroundStyle(DS.Colors.accent)
                            Text(workout.title)
                                .font(.headline.bold())
                                .foregroundStyle(DS.Colors.primaryText)
                            Spacer()
                            if let time = workout.totalTime {
                                Text(time)
                                    .font(.subheadline.bold())
                                    .foregroundStyle(DS.Colors.accent)
                            }
                        }
                        
                        if let hr = workout.hrTarget {
                            Text("Target HR: \(hr)")
                                .font(.caption)
                                .foregroundStyle(DS.Colors.outline)
                        }
                        
                        if !workout.steps.isEmpty {
                            VStack(alignment: .leading, spacing: 8) {
                                ForEach(Array(workout.steps.enumerated()), id: \.offset) { index, step in
                                    HStack(alignment: .top, spacing: 10) {
                                        VStack(spacing: 0) {
                                            Circle()
                                                .fill(isAdapted ? DS.Colors.accent : stepColor(for: step.type))
                                                .frame(width: 8, height: 8)
                                                .padding(.top, 4)
                                            
                                            if index < workout.steps.count - 1 {
                                                Rectangle()
                                                    .fill(DS.Colors.outline.opacity(0.3))
                                                    .frame(width: 1, height: 20)
                                            }
                                        }
                                        
                                        VStack(alignment: .leading, spacing: 2) {
                                            HStack {
                                                Text(step.type.uppercased())
                                                    .font(.system(size: 9, weight: .bold))
                                                    .foregroundStyle(stepColor(for: step.type))
                                                Spacer()
                                                Text(step.duration)
                                                    .font(.system(size: 10, weight: .bold))
                                                    .foregroundStyle(DS.Colors.primaryText)
                                            }
                                            
                                            if let desc = step.description {
                                                Text(desc)
                                                    .font(.caption2)
                                                    .foregroundStyle(DS.Colors.onSurface)
                                            }
                                        }
                                    }
                                }
                            }
                            .padding(.leading, 4)
                        }
                    }
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity)
        .glassCard()
    }
    
    private func stepColor(for type: String) -> Color {
        switch type.lowercased() {
        case "warmup": return .blue
        case "main": return .purple
        case "recovery": return .green
        case "cooldown": return .teal
        default: return .gray
        }
    }
}

// MARK: - Adaptation Comparison Sheet (Telemetry Modal)

struct AdaptationComparisonSheet: View {
    let todayPlan: DayPlan
    let latestRecovery: RecoverySummary?
    @Binding var preferOriginal: Bool
    @Environment(\.dismiss) private var dismiss
    
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    // Subtitle banner
                    Text("Coach modified today's prescription based on overnight recovery telemetry.")
                        .font(.system(size: 13, weight: .regular))
                        .foregroundStyle(DS.Colors.onSurface)
                    
                    // Biometric Triggers Card
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Image(systemName: "waveform.path.ecg")
                                .font(.system(size: 12, weight: .bold))
                                .foregroundStyle(DS.Colors.accent)
                            Text("OVERNIGHT TELEMETRY TRIGGERS")
                                .font(.system(size: 10, weight: .bold))
                                .tracking(1.0)
                                .foregroundStyle(DS.Colors.outline)
                        }
                        
                        HStack(spacing: 10) {
                            if let hrv = latestRecovery?.hrvMs {
                                telemetryMetricTile(
                                    label: "HRV",
                                    value: "\(Int(hrv)) ms",
                                    subtitle: "Telemetry Driver",
                                    isWarning: true
                                )
                            }
                            if let rhr = latestRecovery?.restingHr {
                                telemetryMetricTile(
                                    label: "RESTING HR",
                                    value: "\(rhr) bpm",
                                    subtitle: "Elevated",
                                    isWarning: false
                                )
                            }
                            if let load = latestRecovery?.loadRatio {
                                telemetryMetricTile(
                                    label: "LOAD RATIO",
                                    value: String(format: "%.2f", load),
                                    subtitle: load > 1.3 ? "High Load" : "Balanced",
                                    isWarning: load > 1.3
                                )
                            }
                        }
                        
                        if let reason = todayPlan.adaptation, !reason.isEmpty {
                            HStack(alignment: .top, spacing: 8) {
                                Image(systemName: "sparkles")
                                    .font(.system(size: 12))
                                    .foregroundStyle(DS.Colors.accent)
                                    .padding(.top, 2)
                                Text(reason)
                                    .font(.system(size: 12, weight: .medium))
                                    .foregroundStyle(DS.Colors.primaryText)
                            }
                            .padding(10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color.white.opacity(0.04))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                    }
                    .glassCard()
                    
                    // Side-by-Side / Stacked Session Comparison
                    VStack(alignment: .leading, spacing: 14) {
                        Text("PROTOCOL COMPARISON")
                            .font(.system(size: 10, weight: .bold))
                            .tracking(1.0)
                            .foregroundStyle(DS.Colors.outline)
                        
                        // AI Adapted Card (Recommended)
                        VStack(alignment: .leading, spacing: 10) {
                            HStack {
                                Text("AI ADAPTED PROTOCOL")
                                    .font(.system(size: 11, weight: .bold))
                                    .foregroundStyle(DS.Colors.accent)
                                Spacer()
                                if !preferOriginal {
                                    Text("ACTIVE")
                                        .font(.system(size: 8, weight: .bold))
                                        .foregroundStyle(.black)
                                        .padding(.horizontal, 6)
                                        .padding(.vertical, 2)
                                        .background(DS.Colors.accent)
                                        .clipShape(Capsule())
                                }
                            }
                            
                            if let workouts = todayPlan.workouts, !workouts.isEmpty {
                                ForEach(workouts, id: \.title) { w in
                                    comparisonWorkoutRow(w, isAccent: true)
                                }
                            }
                        }
                        .padding(14)
                        .background(Color.white.opacity(0.05))
                        .clipShape(RoundedRectangle(cornerRadius: DS.Radius.medium))
                        .overlay(
                            RoundedRectangle(cornerRadius: DS.Radius.medium)
                                .stroke(!preferOriginal ? Color.white.opacity(0.4) : Color.white.opacity(0.1), lineWidth: !preferOriginal ? 1.5 : 1)
                        )
                        
                        // Original Blueprint Card
                        VStack(alignment: .leading, spacing: 10) {
                            HStack {
                                Text("ORIGINAL BLUEPRINT")
                                    .font(.system(size: 11, weight: .bold))
                                    .foregroundStyle(DS.Colors.outline)
                                Spacer()
                                if preferOriginal {
                                    Text("ACTIVE (OVERRIDE)")
                                        .font(.system(size: 8, weight: .bold))
                                        .foregroundStyle(.black)
                                        .padding(.horizontal, 6)
                                        .padding(.vertical, 2)
                                        .background(DS.Colors.outline)
                                        .clipShape(Capsule())
                                }
                            }
                            
                            let origWorkouts = todayPlan.originalWorkouts ?? todayPlan.workouts ?? []
                            if !origWorkouts.isEmpty {
                                ForEach(origWorkouts, id: \.title) { w in
                                    comparisonWorkoutRow(w, isAccent: false)
                                }
                            } else {
                                Text("No original session data recorded.")
                                    .font(.caption)
                                    .foregroundStyle(DS.Colors.outline)
                            }
                        }
                        .padding(14)
                        .background(Color.black.opacity(0.3))
                        .clipShape(RoundedRectangle(cornerRadius: DS.Radius.medium))
                        .overlay(
                            RoundedRectangle(cornerRadius: DS.Radius.medium)
                                .stroke(preferOriginal ? Color.white.opacity(0.4) : Color.white.opacity(0.08), lineWidth: 1)
                        )
                    }
                    
                    // Coach's Rationale
                    if let rationale = todayPlan.rationale ?? todayPlan.coachNote, !rationale.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Image(systemName: "brain.head.profile")
                                    .font(.system(size: 11, weight: .bold))
                                    .foregroundStyle(DS.Colors.accent)
                                Text("COACH'S INTENT")
                                    .font(.system(size: 10, weight: .bold))
                                    .tracking(1.0)
                                    .foregroundStyle(DS.Colors.outline)
                            }
                            Text(rationale)
                                .font(.system(size: 12, weight: .regular))
                                .foregroundStyle(DS.Colors.onSurface)
                                .lineSpacing(3)
                        }
                        .glassCard()
                    }
                    
                    // Action Decision Buttons
                    VStack(spacing: 10) {
                        Button {
                            preferOriginal = false
                            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                            dismiss()
                        } label: {
                            HStack {
                                Image(systemName: "checkmark.circle.fill")
                                Text("Keep AI Adaptation (Recommended)")
                            }
                            .font(.system(size: 13, weight: .bold))
                            .foregroundStyle(.black)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                            .background(DS.Colors.accent)
                            .clipShape(RoundedRectangle(cornerRadius: DS.Radius.medium))
                        }
                        
                        Button {
                            preferOriginal = true
                            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                            dismiss()
                        } label: {
                            HStack {
                                Image(systemName: "arrow.counterclockwise")
                                Text("Override & Use Original Plan")
                            }
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(DS.Colors.onSurface)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                            .background(Color.white.opacity(0.06))
                            .clipShape(RoundedRectangle(cornerRadius: DS.Radius.medium))
                            .overlay(
                                RoundedRectangle(cornerRadius: DS.Radius.medium)
                                    .stroke(Color.white.opacity(0.12), lineWidth: 1)
                            )
                        }
                    }
                    .padding(.top, 6)
                }
                .padding(20)
            }
            .background(DS.Colors.background.ignoresSafeArea())
            .navigationTitle("Adaptation Telemetry")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        dismiss()
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 18))
                            .foregroundStyle(DS.Colors.outline)
                    }
                }
            }
        }
        .presentationDetents([.fraction(0.88), .large])
        .presentationDragIndicator(.visible)
    }
    
    private func telemetryMetricTile(label: String, value: String, subtitle: String, isWarning: Bool) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.system(size: 9, weight: .bold))
                .tracking(0.8)
                .foregroundStyle(DS.Colors.outline)
            Text(value)
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(isWarning ? DS.Colors.warning : DS.Colors.primaryText)
            Text(subtitle)
                .font(.system(size: 9, weight: .medium))
                .foregroundStyle(isWarning ? DS.Colors.warning.opacity(0.8) : DS.Colors.outline)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(Color.white.opacity(0.03))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
    
    private func comparisonWorkoutRow(_ w: Workout, isAccent: Bool) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Image(systemName: w.sportIcon)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(isAccent ? DS.Colors.accent : DS.Colors.outline)
                Text(w.title)
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(DS.Colors.primaryText)
                Spacer()
                if let t = w.totalTime {
                    Text(t)
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(isAccent ? DS.Colors.accent : DS.Colors.outline)
                }
            }
            if let hr = w.hrTarget {
                Text("Target HR: \(hr)")
                    .font(.system(size: 10))
                    .foregroundStyle(DS.Colors.outline)
            }
            if !w.steps.isEmpty {
                HStack(spacing: 6) {
                    ForEach(Array(w.steps.prefix(4).enumerated()), id: \.offset) { _, step in
                        Text("\(step.type.prefix(4).uppercased()) \(step.duration)")
                            .font(.system(size: 8, weight: .medium))
                            .foregroundStyle(DS.Colors.outline)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 2)
                            .background(Color.white.opacity(0.04))
                            .clipShape(RoundedRectangle(cornerRadius: 4))
                    }
                }
            }
        }
    }
}

// MARK: - View Modifiers & Extensions



#Preview {
    TodayView()
        .preferredColorScheme(.dark)
}



struct ConnectionSettingsSheet: View {
    @Environment(\.dismiss) private var dismiss
    @ObservedObject private var network = NetworkManager.shared
    @State private var urlText: String = ""
    @State private var isTesting: Bool = false
    
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 24) {
                    // Header card
                    VStack(spacing: 8) {
                        Image(systemName: network.isConnected ? "wifi" : "wifi.slash")
                            .font(.system(size: 48))
                            .foregroundStyle(network.isConnected ? .green : .orange)
                            .symbolEffect(.bounce, value: network.isConnected)
                        
                        Text(network.isConnected ? "Connection Stable" : "Connection Offline")
                            .font(.title2.bold())
                        
                        Text("Phoenix Coach relies on a local FastAPI server running on your Mac for periodization, scraping, and LLM planning.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal)
                    }
                    .padding()
                    .frame(maxWidth: .infinity)
                    .background(Color.orange.opacity(0.05))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    
                    // Input Card
                    VStack(alignment: .leading, spacing: 14) {
                        Text("SERVER CONFIGURATION")
                            .font(.caption.bold())
                            .foregroundStyle(.orange)
                        
                        TextField("http://192.168.x.x:8001", text: $urlText)
                            .textFieldStyle(.roundedBorder)
                            .autocorrectionDisabled()
                            .textInputAutocapitalization(.never)
                            .keyboardType(.URL)
                            .font(.body.monospaced())
                        
                        HStack(spacing: 12) {
                            Button {
                                isTesting = true
                                network.baseURL = urlText
                                Task {
                                    await network.checkConnection()
                                    isTesting = false
                                }
                            } label: {
                                HStack {
                                    if isTesting {
                                        ProgressView()
                                            .controlSize(.small)
                                            .padding(.trailing, 4)
                                    }
                                    Text("Test & Apply")
                                        .bold()
                                }
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 10)
                                .background(Color.orange)
                                .foregroundStyle(.white)
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                            }
                            .disabled(isTesting)
                            
                            Button {
                                network.resetToDefaultURL()
                                urlText = network.baseURL
                            } label: {
                                Text("Reset Default")
                                    .bold()
                                    .frame(maxWidth: .infinity)
                                    .padding(.vertical, 10)
                                    .background(Color(.systemGray5))
                                    .foregroundStyle(.primary)
                                    .clipShape(RoundedRectangle(cornerRadius: 8))
                            }
                        }
                    }
                    .padding()
                    .background(.ultraThinMaterial)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    
                    // Diagnostics Section
                    VStack(alignment: .leading, spacing: 12) {
                        Text("DIAGNOSTICS & STATUS")
                            .font(.caption.bold())
                            .foregroundStyle(.secondary)
                        
                        statusRow(title: "Backend FastAPI", status: network.isConnected ? "Online" : "Offline", isOk: network.isConnected)
                        statusRow(title: "Mac Ollama API", status: network.isConnected ? (network.isOllamaConnected ? "Running" : "Offline") : "N/A", isOk: network.isConnected && network.isOllamaConnected)
                        statusRow(title: "Device Local LLM", status: "Ready", isOk: true)
                    }
                    .padding()
                    .background(Color(.secondarySystemGroupedBackground))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    
                    // Troubleshooting Guide
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Troubleshooting Guide")
                            .font(.headline)
                        
                        bulletPoint("Make sure your Mac backend is running via the command `PYTHONPATH=. python3 backend/main.py` in the workspace.")
                        bulletPoint("Ensure both your iPhone/device and Mac are connected to the exact same Wi-Fi network.")
                        bulletPoint("Check that the app has internet access. You can test in your Mac browser at `https://phoenix-coach.onrender.com/health`.")
                    }
                    .padding(.horizontal, 4)
                }
                .padding()
            }
            .navigationTitle("Connection Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .bold()
                    .tint(.white)
                }
            }
            .onAppear {
                urlText = network.baseURL
            }
        }
    }
    
    private func statusRow(title: String, status: String, isOk: Bool) -> some View {
        HStack {
            Text(title)
                .font(.subheadline)
            Spacer()
            HStack(spacing: 6) {
                Circle()
                    .fill(isOk ? Color.green : Color.red)
                    .frame(width: 8, height: 8)
                Text(status)
                    .font(.caption.bold())
                    .foregroundStyle(isOk ? .green : .red)
            }
        }
    }
    
    private func bulletPoint(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Text("•")
                .foregroundStyle(DS.Colors.warning)
            Text(text)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}

enum MetricType {
    case hrv, rhr, load
}

struct MetricChartSheet: View {
    @Environment(\.dismiss) var dismiss
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
                DS.Colors.background.ignoresSafeArea()
                
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
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundStyle(DS.Colors.accent)
                        .font(.body.bold())
                }
            }
        }
        .presentationDetents([.medium])
        .presentationBackground(.ultraThinMaterial)
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
