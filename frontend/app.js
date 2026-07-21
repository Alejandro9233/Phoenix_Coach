const API_BASE = "http://localhost:8001";
let activePlanData = null;
let activeContextData = null;
let selectedDayName = null;

// Page Startup & Event Setup
document.addEventListener("DOMContentLoaded", () => {
    // Initialize Navigation Tabs
    const navButtons = document.querySelectorAll(".nav-btn");
    const tabContents = document.querySelectorAll(".tab-content");

    navButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            const targetTab = btn.getAttribute("data-tab");
            
            navButtons.forEach(b => b.classList.remove("active"));
            tabContents.forEach(t => t.classList.remove("active"));
            
            btn.classList.add("active");
            document.getElementById(targetTab).classList.add("active");
            
            // Re-render Lucide icons on tab switch just in case
            if (window.lucide) {
                window.lucide.createIcons();
            }
        });
    });

    // Initialize Auditor Tabs
    const auditorTabs = document.querySelectorAll(".auditor-tab");
    const auditCodes = document.querySelectorAll(".audit-code");

    auditorTabs.forEach(tab => {
        tab.addEventListener("click", () => {
            const targetAudit = tab.getAttribute("data-audit");
            
            auditorTabs.forEach(t => t.classList.remove("active"));
            auditCodes.forEach(c => c.classList.remove("active"));
            
            tab.classList.add("active");
            document.getElementById(targetAudit).classList.add("active");
        });
    });

    // Auditor Manual Refresh Button
    document.getElementById("refresh-auditor-btn").addEventListener("click", () => {
        updateAuditorData();
    });

    // AI Chat Input Events
    const chatInput = document.getElementById("chat-input");
    const chatSendBtn = document.getElementById("chat-send-btn");
    
    chatSendBtn.addEventListener("click", sendChatMessage);
    chatInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") {
            sendChatMessage();
        }
    });

    // Adaptation Simulator Action Button
    document.getElementById("trigger-adaptation-btn").addEventListener("click", executeDynamicAdaptation);

    // Initial Data Fetch
    checkBackendHealth();
    fetchInitialData();
});

// 1. Connection Status Checking
async function checkBackendHealth() {
    const statusDot = document.getElementById("api-status-dot");
    const statusText = document.getElementById("api-status-text");

    try {
        const response = await fetch(`${API_BASE}/health`);
        const data = await response.json();
        
        if (data.backend === "ok") {
            if (data.ollama === "connected") {
                statusDot.className = "status-dot green";
                statusText.textContent = "Coach: Online (Qwen3)";
            } else {
                statusDot.className = "status-dot orange";
                statusText.textContent = "Ollama: Disconnected";
            }
        } else {
            statusDot.className = "status-dot red";
            statusText.textContent = "Server Error";
        }
    } catch (error) {
        statusDot.className = "status-dot red";
        statusText.textContent = "API: Offline";
        console.error("Health check failure:", error);
    }
}

// 2. Fetch Initial Dashboard / Planning Data
async function fetchInitialData() {
    try {
        // Fetch athlete profile
        const athleteRes = await fetch(`${API_BASE}/athlete/profile`);
        const athleteData = await athleteRes.json();
        renderAthleteProfile(athleteData);

        // Fetch deterministic periodization training context
        const contextRes = await fetch(`${API_BASE}/training-context`);
        activeContextData = await contextRes.json();
        renderPeriodizationBrain(activeContextData);

        // Fetch weekly plan and compliance status
        const planRes = await fetch(`${API_BASE}/weekly-plan/status`);
        activePlanData = await planRes.json();
        renderWeeklyPlanStrip(activePlanData);

        // Fetch EvoLab history and charts (Tab 4)
        const dashboardRes = await fetch(`${API_BASE}/dashboard`);
        const dashboardData = await dashboardRes.json();
        renderEvoLabStats(dashboardData);

        // Populating Initial Auditor snapshots
        updateAuditorData();

    } catch (error) {
        console.error("Initial data load error:", error);
    }
}

// 3. Render Profile Info in Sidebar
function renderAthleteProfile(profile) {
    if (profile && profile.name) {
        document.querySelector("#athlete-profile .name").textContent = profile.name;
        document.querySelector("#athlete-profile .vo2").textContent = `VO2max: ${profile.vo2_max || 60}`;
    }
}

// 4. Render Tab 1: Periodization Brain
function renderPeriodizationBrain(ctx) {
    if (!ctx) return;

    // Update active badges
    const currentPhaseBadge = document.getElementById("current-phase-badge");
    currentPhaseBadge.textContent = ctx.phase_name || "Foundation";
    
    document.getElementById("phase-priorities").textContent = ctx.phase_priorities || "Build base aerobic fitness.";
    document.getElementById("weeks-to-race-val").textContent = ctx.weeks_to_race != null ? `${ctx.weeks_to_race} wks` : "None set";
    document.getElementById("active-week-val").textContent = ctx.phase_week ? `Week ${ctx.phase_week}` : "Week 1";
    document.getElementById("total-phase-weeks-val").textContent = ctx.phase_total_weeks ? `${ctx.phase_total_weeks} weeks` : "8 weeks";

    // 29-Week Roadmap milestones
    const stepsContainer = document.getElementById("timeline-steps-container");
    stepsContainer.innerHTML = "";
    
    const phases = [
        { key: "foundation", label: "Foundation" },
        { key: "marathon_base", label: "Base" },
        { key: "marathon_build", label: "Build" },
        { key: "marathon_peak", label: "Peak" },
        { key: "taper", label: "Taper & Race" }
    ];

    let activePhaseIndex = phases.findIndex(p => p.key === ctx.phase);
    if (activePhaseIndex === -1) activePhaseIndex = 0;

    phases.forEach((p, idx) => {
        const step = document.createElement("div");
        step.className = `timeline-step ${idx < activePhaseIndex ? 'completed' : ''} ${idx === activePhaseIndex ? 'active' : ''}`;
        
        step.innerHTML = `
            <div class="step-dot"></div>
            <span class="step-label">${p.label}</span>
        `;
        stepsContainer.appendChild(step);
    });

    // Update timeline progress bar width
    const timelineProgress = document.getElementById("timeline-progress-bar");
    const progressPercent = (activePhaseIndex / (phases.length - 1)) * 100;
    timelineProgress.style.width = `${progressPercent}%`;

    // 3:1 Cycle Wave
    const cycleWeekBadge = document.getElementById("cycle-week-badge");
    cycleWeekBadge.textContent = ctx.is_recovery_week ? "Recovery Week" : `Build ${ctx.cycle_week}/3`;
    if (ctx.is_recovery_week) {
        cycleWeekBadge.className = "badge purple-badge";
    } else {
        cycleWeekBadge.className = "badge blue-badge";
    }

    document.getElementById("cycle-recovery-note").textContent = ctx.recovery_note || "Normal progression load.";

    // Wave indicator pulse position
    const wavePulse = document.getElementById("wave-pulse-indicator");
    const cycleWeek = ctx.cycle_week || 1;
    const wavePulsePositions = {
        1: { cx: 100, cy: 40 },
        2: { cx: 200, cy: 30 },
        3: { cx: 300, cy: 20 },
        4: { cx: 400, cy: 50 }
    };
    
    const activeNode = document.getElementById(`node-week-${cycleWeek}`);
    if (activeNode) {
        document.querySelectorAll(".wave-node").forEach(node => node.classList.remove("active"));
        activeNode.classList.add("active");
    }

    const pos = wavePulsePositions[cycleWeek];
    if (pos && wavePulse) {
        wavePulse.setAttribute("cx", pos.cx);
        wavePulse.setAttribute("cy", pos.cy);
    }

    // Workout constraints lists
    const allowedList = document.getElementById("allowed-workouts-list");
    allowedList.innerHTML = "";
    if (ctx.workout_menu) {
        Object.entries(ctx.workout_menu).forEach(([sport, workouts]) => {
            if (workouts && workouts.length > 0) {
                workouts.forEach(w => {
                    const li = document.createElement("li");
                    li.innerHTML = `<span style="text-transform: capitalize; font-weight:600; color:var(--accent-green);">${sport}:</span> ${w}`;
                    allowedList.appendChild(li);
                });
            }
        });
    }

    const forbiddenList = document.getElementById("forbidden-workouts-list");
    forbiddenList.innerHTML = "";
    if (ctx.forbidden_workouts && ctx.forbidden_workouts.length > 0) {
        ctx.forbidden_workouts.forEach(w => {
            const li = document.createElement("li");
            li.textContent = w;
            forbiddenList.appendChild(li);
        });
    } else {
        const li = document.createElement("li");
        li.textContent = "None. All workout types are accessible.";
        forbiddenList.appendChild(li);
    }

    document.getElementById("forbidden-workouts-reason").textContent = ctx.forbidden_reason || "None.";

    if (window.lucide) {
        window.lucide.createIcons();
    }
}

// 5. Render Tab 2: Weekly Schedule & Stats
function renderWeeklyPlanStrip(plan) {
    if (!plan) return;

    // Render Progress stats bar
    const progress = plan.week_progress;
    if (progress) {
        document.getElementById("plan-sessions-done").textContent = progress.sessions_completed;
        document.getElementById("plan-sessions-planned").textContent = progress.sessions_planned;
        document.getElementById("plan-hours-done").textContent = progress.hours_done.toFixed(1);
        document.getElementById("plan-hours-planned").textContent = progress.hours_planned.toFixed(1);
        document.getElementById("plan-total-load").textContent = progress.total_training_load;
        document.getElementById("plan-compliance-pct").textContent = `${progress.completion_pct}%`;

        // Style compliance percentage color
        const complianceText = document.getElementById("plan-compliance-pct");
        if (progress.completion_pct >= 85) {
            complianceText.style.color = "var(--accent-green)";
        } else if (progress.completion_pct >= 50) {
            complianceText.style.color = "var(--accent-warn)";
        } else {
            complianceText.style.color = "var(--accent-red)";
        }
    }

    // Weekly Summary Cards
    const summary = plan.week_summary;
    if (summary) {
        document.getElementById("week-focus-text").textContent = summary.focus;
        document.getElementById("week-focus-rationale").textContent = summary.rationale;
    }

    // Mon-Sun strip list
    const stripContainer = document.getElementById("calendar-days-strip");
    stripContainer.innerHTML = "";

    const daysOrdered = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
    
    daysOrdered.forEach(dayName => {
        const dayData = plan.days[dayName];
        if (!dayData) return;

        const mainWorkout = dayData.workouts && dayData.workouts.length > 0 ? dayData.workouts[0] : { sport: "rest", title: "Rest Day" };
        const actual = dayData.actual || { completed: false, skipped: false, is_rest: true, is_today: false };

        const dayCard = document.createElement("div");
        dayCard.className = `calendar-day-card glass ${actual.is_today ? 'active' : ''}`;
        dayCard.setAttribute("data-day", dayName);

        // Sport emoji match
        const sportEmojis = {
            running: "🏃‍♂️",
            cycling: "🚴‍♂️",
            swimming: "🏊‍♂️",
            strength: "🏋️‍♂️",
            rest: "🛌"
        };
        const emoji = sportEmojis[mainWorkout.sport] || "📅";

        // Status badge setup
        let statusText = "Upcoming";
        let statusClass = "status-pending";

        if (actual.is_past) {
            if (actual.is_rest) {
                statusText = "Rest";
                statusClass = "status-pending";
            } else if (actual.completed) {
                statusText = "Completed";
                statusClass = "status-completed";
            } else if (actual.skipped) {
                statusText = "Missed";
                statusClass = "status-missed";
            } else {
                statusText = "Partial";
                statusClass = "status-partial";
            }
        } else if (actual.is_today) {
            statusText = "Today";
            statusClass = "status-partial";
        }

        dayCard.innerHTML = `
            <span class="day-header-lbl">${dayName.slice(0, 3)}</span>
            <span class="day-sport-icon">${emoji}</span>
            <span class="day-status-pill ${statusClass}">${statusText}</span>
            <span style="font-size: 0.8rem; font-weight:600; text-align:center; max-width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${mainWorkout.title}</span>
        `;

        dayCard.addEventListener("click", () => {
            document.querySelectorAll(".calendar-day-card").forEach(c => c.classList.remove("active"));
            dayCard.classList.add("active");
            showDayDeepDive(dayName, dayData);
        });

        stripContainer.appendChild(dayCard);
        
        // Auto-select today if rendering
        if (actual.is_today) {
            showDayDeepDive(dayName, dayData);
            dayCard.classList.add("active");
        }
    });

    if (window.lucide) {
        window.lucide.createIcons();
    }
}

// 6. Day Detail Deep Dive Rendering
function showDayDeepDive(dayName, dayData) {
    selectedDayName = dayName;
    document.getElementById("panel-placeholder-text").classList.add("hidden");
    const content = document.getElementById("panel-real-content");
    content.classList.remove("hidden");

    // Header values
    document.getElementById("detail-day-name").textContent = `${dayName} Plan & Rationale`;
    document.getElementById("detail-day-sport").textContent = dayData.workouts && dayData.workouts.length > 0 ? dayData.workouts[0].sport : "Rest";
    
    // Status text
    const statusDot = document.getElementById("detail-day-status");
    const actual = dayData.actual || {};
    
    statusDot.textContent = actual.is_today ? "Today's Target" : (actual.completed ? "Completed ✅" : (actual.skipped ? "Skipped ❌" : "Planned ⏳"));
    statusDot.className = `badge ${actual.completed ? 'green-badge' : (actual.skipped ? 'danger-badge' : 'blue-badge')}`;

    // Comparison column
    const mainWorkout = dayData.workouts && dayData.workouts.length > 0 ? dayData.workouts[0] : { sport: "rest", title: "Rest Day", steps: [], total_time: "N/A", hr_target: "N/A" };
    document.getElementById("detail-plan-title").textContent = mainWorkout.title;

    // Parse actual activity if any
    const activityMatched = actual.activities && actual.activities.length > 0 ? actual.activities[0] : null;
    const actualTimeStr = activityMatched ? `${activityMatched.duration_min} min` : "None";
    document.getElementById("detail-compare-duration").textContent = `${mainWorkout.total_time} / ${actualTimeStr}`;

    const actualHrStr = activityMatched && activityMatched.avg_hr ? `${activityMatched.avg_hr} bpm` : "None";
    document.getElementById("detail-compare-hr").textContent = `${mainWorkout.hr_target || 'N/A'} / ${actualHrStr}`;

    // Distance & Load row toggle
    const distRow = document.getElementById("detail-compare-distance-row");
    const loadRow = document.getElementById("detail-compare-load-row");

    if (activityMatched && activityMatched.distance_km > 0) {
        distRow.classList.remove("hidden");
        document.getElementById("detail-compare-distance").textContent = `${activityMatched.distance_km} km`;
    } else {
        distRow.classList.add("hidden");
    }

    if (activityMatched && activityMatched.training_load > 0) {
        loadRow.classList.remove("hidden");
        document.getElementById("detail-compare-load").textContent = `${activityMatched.training_load} TL`;
    } else {
        loadRow.classList.add("hidden");
    }

    // Workout steps
    const stepsList = document.getElementById("detail-workout-steps");
    stepsList.innerHTML = "";
    if (mainWorkout.steps && mainWorkout.steps.length > 0) {
        mainWorkout.steps.forEach(s => {
            const li = document.createElement("li");
            li.innerHTML = `<strong>${s.type.toUpperCase()}</strong> [${s.duration || 'N/A'}, Z${s.zone || '1'}]: ${s.description}`;
            stepsList.appendChild(li);
        });
    } else {
        const li = document.createElement("li");
        li.textContent = mainWorkout.sport === "rest" ? "No planned steps. Active recovery or sleep recommended." : "Easy base training.";
        stepsList.appendChild(li);
    }

    // Coach Thinking bubble
    document.getElementById("detail-coach-rationale").textContent = `"${dayData.rationale || 'No explanation provided.'}"`;
    document.getElementById("detail-coach-note").textContent = `"${dayData.coach_note || 'Enjoy the session!'}"`;

    // Compliance Insights
    const complianceNotes = document.getElementById("detail-compliance-notes");
    if (activityMatched && actual.compliance && actual.compliance.length > 0) {
        const complianceObj = actual.compliance[0];
        complianceNotes.innerHTML = `
            <strong>Score: ${complianceObj.score}/100</strong> (${complianceObj.status.toUpperCase()})
            <br><span style="color:var(--text-secondary); font-size:0.8rem;">${complianceObj.notes}</span>
        `;
    } else if (actual.is_past && mainWorkout.sport !== "rest") {
        complianceNotes.innerHTML = `<span style="color:var(--accent-red); font-weight:600;">Missed Workout</span> — No matching session scraped from COROS watch.`;
    } else if (mainWorkout.sport === "rest") {
        complianceNotes.textContent = "Rest Day scheduled. Compliance matches fully.";
    } else {
        complianceNotes.textContent = "Planned session. Awaiting COROS scraper update.";
    }

    // Toggle simulator display (Only display if selected day is TODAY)
    const simulator = document.getElementById("adaptation-simulator-panel");
    const adaptedResult = document.getElementById("adapted-result-box");
    
    // Hide previous simulation output on day change
    adaptedResult.classList.add("hidden");

    if (actual.is_today) {
        simulator.classList.remove("hidden");
    } else {
        simulator.classList.add("hidden");
    }
}

// 7. Execute Dynamic Adaptation Simulator
async function executeDynamicAdaptation() {
    const triggerBtn = document.getElementById("trigger-adaptation-btn");
    const spinner = document.getElementById("sim-spinner");
    const adaptedResult = document.getElementById("adapted-result-box");

    // Grab dropdown values
    const hrvVal = document.getElementById("sim-hrv").value;
    const rhrVal = document.getElementById("sim-rhr").value;
    const sorenessVal = document.getElementById("sim-soreness").value;

    triggerBtn.disabled = true;
    spinner.classList.remove("hidden");
    adaptedResult.classList.add("hidden");

    try {
        const response = await fetch(`${API_BASE}/weekly-plan/adapt-today`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                hrv: hrvVal,
                rhr: rhrVal,
                soreness: sorenessVal
            })
        });

        const adaptedWorkout = await response.json();
        
        // Render results
        document.getElementById("adapted-workout-title").textContent = adaptedWorkout.title || "Rest Recovery";
        document.getElementById("adapted-workout-time").textContent = adaptedWorkout.total_time || "Rest";
        document.getElementById("adapted-workout-hr").textContent = adaptedWorkout.hr_target || "Easy";
        document.getElementById("adapted-workout-rationale").textContent = adaptedWorkout.adaptation || adaptedWorkout.rationale || "Plan adjusted to optimize health.";

        spinner.classList.add("hidden");
        adaptedResult.classList.remove("hidden");
        
        // Instantly reload plan statuses to show the adapted icon/title on the calendar!
        const planRes = await fetch(`${API_BASE}/weekly-plan/status`);
        activePlanData = await planRes.json();
        renderWeeklyPlanStrip(activePlanData);

        // Update prompt auditor with fresh context snapshot
        updateAuditorData();

    } catch (error) {
        console.error("Adaptation error:", error);
        alert("Failed to adapt workout. Check that backend server is running.");
        spinner.classList.add("hidden");
    } finally {
        triggerBtn.disabled = false;
    }
}

// 8. Stream Sandbox Chat & Auditor updates
async function sendChatMessage() {
    const input = document.getElementById("chat-input");
    const text = input.value.trim();
    if (!text) return;

    input.value = "";
    
    // Append athlete bubble
    const messagesContainer = document.getElementById("chat-messages-container");
    const athleteBubble = document.createElement("div");
    athleteBubble.className = "msg message-athlete";
    athleteBubble.innerHTML = `<p>${text}</p>`;
    messagesContainer.appendChild(athleteBubble);
    
    // Scroll to bottom
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    // Create placeholder for coach bubble
    const coachBubble = document.createElement("div");
    coachBubble.className = "msg message-coach";
    coachBubble.innerHTML = `<p><em>Thinking...</em></p>`;
    messagesContainer.appendChild(coachBubble);

    try {
        const response = await fetch(`${API_BASE}/chat`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: text })
        });

        if (!response.ok) throw new Error("Network offline");

        coachBubble.innerHTML = `<p></p>`; // Clear thinking
        const bubbleText = coachBubble.querySelector("p");

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value, { stream: true });
            const lines = chunk.split("\n");
            
            for (const line of lines) {
                if (line.startsWith("data: ")) {
                    const dataStr = line.slice(6).trim();
                    if (dataStr === "[DONE]") break;
                    
                    try {
                        const parsed = JSON.parse(dataStr);
                        if (parsed.token) {
                            bubbleText.textContent += parsed.token;
                            messagesContainer.scrollTop = messagesContainer.scrollHeight;
                        }
                    } catch (e) {
                        // ignore malformed packets
                    }
                }
            }
        }

        // Fetch new snapshots to populate auditor
        updateAuditorData(text);

    } catch (error) {
        console.error("Chat error:", error);
        coachBubble.innerHTML = `<p style="color:var(--accent-red);">I encountered a connection error. Verify Ollama is running (` + "`" + `ollama serve` + "`" + `) and try again.</p>`;
    }
}

// 9. Update Live Prompt Auditor text values
async function updateAuditorData(lastUserQuery = "") {
    const sysCode = document.getElementById("system-prompt-audit");
    const ctxCode = document.getElementById("context-snapshot-audit");
    const ragCode = document.getElementById("rag-audit");

    // Copy system instructions prompt
    sysCode.innerHTML = `<code>You are Phoenix, an elite triathlon coach. You are coaching a single athlete who trains for triathlon (swim, bike, run) and also does strength training.

RULES:
1. Always base your decisions on the athlete's data and the coaching principles provided
2. Never prescribe high-intensity work when the athlete shows signs of fatigue (negative TIB, elevated RHR, low HRV)
3. Follow the 80/20 rule: most training should be easy (Zone 1-2)
4. Never schedule 3 consecutive hard days
5. If load ratio is >1.5, prescribe only recovery
6. Be concise and direct — the athlete wants clear instructions, not essays</code>`;

    // Fetch or reconstruct Context snapshot
    if (activeContextData) {
        ctxCode.textContent = `=== TRAINING_CONTEXT snapshots injected into LLM Prompt ===\n` + JSON.stringify(activeContextData, null, 2);
    } else {
        ctxCode.textContent = "Awaiting training context metrics...";
    }

    // Display retrieved RAG context based on active context
    if (activeContextData) {
        const query = lastUserQuery || `${activeContextData.phase_name} base volume progression training guidelines`;
        ragCode.innerHTML = `<code>[RAG Chromadb Query]: "${query}"

[Chunk 1: knowledge/periodization.md]:
Triathlon training periodization demands gradual volume shifts. Foundation phase sets aerobic resilience (90/10 intensity layout, Zone 2 cap, strict weekly volume ceilings).

[Chunk 2: knowledge/recovery_rules.md]:
When Overnight HRV falls >10% below average baseline or resting heart rate spikes by +5 bpm, adapt volume down 20-25%. If both occur, schedule REST days.

[Chunk 3: knowledge/workout_types.md]:
Foundation phase forbids high-intensity VO2max track sets, sweet spot cycles, or bricks to secure soft tissue recovery and joint adaptation before load escalation.</code>`;
    } else {
        ragCode.textContent = "Awaiting active query data...";
    }
}

// 10. Render Tab 4: EvoLab Stats & Historical Charts
let loadChartInstance = null;
let hrvChartInstance = null;

function renderEvoLabStats(data) {
    const { athlete, activities, recovery } = data;

    if (!recovery || recovery.length === 0) return;

    // Load stats metrics
    const latest = recovery[0];
    document.getElementById("cti-val").textContent = latest.cti ? latest.cti.toFixed(1) : '--';
    document.getElementById("ati-val").textContent = latest.ati ? latest.ati.toFixed(1) : '--';
    document.getElementById("tib-val").textContent = latest.tib ? latest.tib.toFixed(1) : '--';
    document.getElementById("ratio-val").textContent = latest.load_ratio ? latest.load_ratio.toFixed(2) : '--';

    // Form Status Text
    const tibStatus = document.getElementById("tib-status");
    const tib = latest.tib || 0;
    if (tib > 0) {
        tibStatus.innerHTML = `<span style="color:var(--accent-green); font-weight:600;"><i data-lucide="check-circle" style="width:14px;height:14px;vertical-align:middle;margin-right:2px;"></i> Fresh</span>`;
    } else if (tib < -20) {
        tibStatus.innerHTML = `<span style="color:var(--accent-red); font-weight:600;"><i data-lucide="alert-circle" style="width:14px;height:14px;vertical-align:middle;margin-right:2px;"></i> Fatigue accumulated</span>`;
    } else {
        tibStatus.innerHTML = `<span style="color:var(--accent-blue); font-weight:600;"><i data-lucide="activity" style="width:14px;height:14px;vertical-align:middle;margin-right:2px;"></i> Productive</span>`;
    }

    // Ratio Badge Style
    const ratioStatus = document.getElementById("ratio-status");
    const ratio = latest.load_ratio || 0;
    if (ratio < 0.8) {
        ratioStatus.textContent = "Detraining";
        ratioStatus.className = "status-badge status-warn";
    } else if (ratio <= 1.5) {
        ratioStatus.textContent = "Optimal";
        ratioStatus.className = "status-badge status-safe";
    } else {
        ratioStatus.textContent = "Overreaching";
        ratioStatus.className = "status-badge status-danger";
    }

    // Scraped Activities list
    const container = document.getElementById("activities-container");
    container.innerHTML = "";
    activities.forEach(act => {
        const dateObj = new Date(act.start_time);
        const dateStr = dateObj.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        const row = document.createElement("div");
        row.className = "activity-item";
        row.innerHTML = `
            <span>${dateStr}</span>
            <span><span class="sport-tag">${act.sport}</span></span>
            <span>${(act.distance_m / 1000).toFixed(2)} km</span>
            <span style="font-weight: 600; color: #818cf8;">${Math.round(act.training_load || 0)} TL</span>
        `;
        container.appendChild(row);
    });

    // History tables
    const tableBody = document.getElementById("recovery-body");
    tableBody.innerHTML = "";
    recovery.slice(0, 15).forEach(r => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${r.date}</td>
            <td>${r.resting_hr || '--'}</td>
            <td>${r.hrv_ms ? Math.round(r.hrv_ms) : '--'}</td>
            <td>${r.vo2_max || '--'}</td>
            <td>${r.ati ? Math.round(r.ati) : '--'}</td>
            <td>${r.cti ? Math.round(r.cti) : '--'}</td>
            <td>${r.load_ratio ? r.load_ratio.toFixed(2) : '--'}</td>
        `;
        tableBody.appendChild(tr);
    });

    // Render Charts
    buildLoadChart(recovery.slice(0, 30));
    buildHrvChart(recovery.slice(0, 30));
}

// 11. Historical Chart builders
function buildLoadChart(recovery) {
    const canvas = document.getElementById('loadChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    
    if (loadChartInstance) {
        loadChartInstance.destroy();
    }

    const reversed = [...recovery].reverse();
    const labels = reversed.map(r => {
        const parts = r.date.split('-');
        const d = new Date(parts[0], parts[1] - 1, parts[2]);
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    });

    loadChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Fitness (CTI)',
                    data: reversed.map(r => r.cti),
                    borderColor: '#38bdf8',
                    backgroundColor: 'rgba(56, 189, 248, 0.05)',
                    fill: true,
                    tension: 0.4
                },
                {
                    label: 'Fatigue (ATI)',
                    data: reversed.map(r => r.ati),
                    borderColor: '#f43f5e',
                    borderDash: [5, 5],
                    fill: false,
                    tension: 0.4
                },
                {
                    label: 'Daily Load',
                    data: reversed.map(r => r.training_load),
                    type: 'bar',
                    backgroundColor: 'rgba(129, 140, 248, 0.3)',
                    borderRadius: 4
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { labels: { color: '#94a3b8', font: { family: 'Outfit' } } }
            },
            scales: {
                y: { grid: { color: 'rgba(255, 255, 255, 0.04)' }, ticks: { color: '#94a3b8' } },
                x: { grid: { display: false }, ticks: { color: '#94a3b8' } }
            }
        }
    });
}

function buildHrvChart(recovery) {
    const canvas = document.getElementById('hrvChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    if (hrvChartInstance) {
        hrvChartInstance.destroy();
    }

    const reversed = [...recovery].reverse();
    const labels = reversed.map(r => {
        const parts = r.date.split('-');
        const d = new Date(parts[0], parts[1] - 1, parts[2]);
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    });

    hrvChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Overnight HRV',
                    data: reversed.map(r => r.hrv_ms),
                    borderColor: '#818cf8',
                    backgroundColor: 'rgba(129, 140, 248, 0.05)',
                    pointBackgroundColor: '#818cf8',
                    fill: true,
                    tension: 0.3
                },
                {
                    label: 'Baseline',
                    data: reversed.map(r => r.hrv_baseline),
                    borderColor: 'rgba(148, 163, 184, 0.4)',
                    borderWidth: 1.5,
                    borderDash: [10, 5],
                    pointRadius: 0,
                    fill: false
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { labels: { color: '#94a3b8' } }
            },
            scales: {
                y: { 
                    grid: { color: 'rgba(255, 255, 255, 0.04)' }, 
                    ticks: { color: '#94a3b8' },
                    suggestedMin: 40
                },
                x: { grid: { display: false }, ticks: { color: '#94a3b8' } }
            }
        }
    });
}
