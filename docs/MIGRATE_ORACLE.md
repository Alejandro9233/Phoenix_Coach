# Migration: Render → Oracle Cloud (free ARM VM)

Why: Render free tier gives 0.1 vCPU — Chromium can't finish COROS's heavy pages
inside the 60s waits (2026-09-01: three morning attempts, recovery metrics never
landed). Oracle Always Free gives 2 ARM OCPUs + 12 GB. CPU is the bottleneck;
this removes it for $0.

Safety rule for the whole migration: **Render stays alive and untouched until
Oracle survives 7 consecutive clean morning refreshes.** Rollback at any point
= switch `baseURL` back in the app (Profile). The DB is Supabase — shared,
external, and never moves. Nothing here touches it.

Do NOT set `TIMEZONE` in the VM env (breaks travel; phone reports it).
Keep `RENDER=true` during cutover — flip it only in Phase 6, one change at a time.

## Phase 0 — Prep (Claude, repo-side, ~30 min)

- [ ] `deploy/setup_server.sh` — one-shot: apt, unattended-upgrades, Python venv,
      clone, pip install, `playwright install --with-deps chromium`, swap file,
      Caddy, systemd units, iptables fix.
- [ ] `deploy/phoenix.service` — systemd unit, restart-on-failure, boots on start.
- [ ] `deploy/Caddyfile` — reverse proxy :443 → :8001, SSE-safe (`flush_interval -1`).
- [ ] `deploy/deploy.sh` — git pull, pip install if requirements changed,
      restart, `/health` check.
- [ ] `.github/workflows/deploy.yml` — push to main → SSH → `deploy.sh`
      (inactive until secrets are set in Phase 5).
- [ ] Verify `requirements.txt` is pinned and complete.

## Phase 1 — Provision (Alex, ~30-60 min)

- [ ] Oracle Cloud account (card required, not charged). Home region: pick one
      with A1 capacity — Frankfurt or Singapore provision in minutes; US East
      often says "out of host capacity". Home region is permanent.
- [ ] Create instance: **VM.Standard.A1.Flex, 2 OCPU, 12 GB** (the full
      Always-Free allowance since the June 2026 cut — claim all of it in one VM),
      Ubuntu 24.04 (aarch64), boot volume 100 GB.
- [ ] Upload your SSH public key during creation. Note the public IP.
- [ ] VCN security list: allow inbound TCP 80 and 443 from 0.0.0.0/0. Leave 22.
- [ ] **GATE: `ssh ubuntu@<IP>` works from the Mac.** Then hand Claude the IP.

Gotcha: Oracle Ubuntu images ship a restrictive iptables ruleset *inside* the
VM — opening the cloud security list is not enough. `setup_server.sh` fixes the
in-VM rules; don't debug "connection refused" before it has run.

## Phase 2 — Server setup (both, ~1 h)

- [ ] Run `setup_server.sh` on the VM.
- [ ] Build the VM `.env` and `scp` it across — secrets never enter git, and
      **don't copy the local `.env` blindly**: keep `DATABASE_URL` (Supabase),
      COROS creds and `GROQ_API_KEY` from it, but take `COACHING_MODEL` from
      the **Render dashboard** (the local `.env` pins a retired model), add
      `RENDER=true` and `PLAYWRIGHT_BROWSERS_PATH=0` (also dashboard-only
      today). Never: `TIMEZONE`, `OLLAMA_MODEL`, `SCRAPER_DEBUG_DIR`.
- [ ] `systemctl start phoenix` → local `curl :8001/health` 200.
- [ ] **GATE: `scripts/scraper_health_check.py` passes on the VM — ARM Chromium
      against real COROS, `missing=[]`.** This is the whole point of the move;
      if it fails here, stop and diagnose before spending another minute.
- [ ] After the gate: `venv/bin/pip freeze > requirements.lock` on the VM and
      commit it — the full transitive graph, resolved on the real
      Linux/3.12/aarch64 target. `deploy.sh` prefers the lock from then on;
      `requirements.txt` stays the human-edited top level.

## Phase 3 — Domain + HTTPS (~20 min)

- [ ] DuckDNS subdomain (e.g. `phoenix-coach.duckdns.org`) → VM IP; updater on
      a systemd timer.
- [ ] Caddy serves it with auto Let's Encrypt.
- [ ] **GATE: `https://<domain>/health` returns 200 from the phone on
      cellular** (not home wifi — proves the path the app will really use).

## Phase 4 — Parallel dry-run (both, 1-2 days, Render still primary)

- [ ] On Oracle: `POST /smart-refresh/start`, watch `journalctl -u phoenix -f`.
      Don't run within ~30 min of a Render scrape — COROS throttles rapid
      re-logins (seen 2026-09-01, attempt 3).
- [ ] `GET /weekly-plan` returns the same plan as Render (same DB — must match).
- [ ] Chat streams through Caddy (SSE) without buffering.
- [ ] **GATE: one full refresh cycle on Oracle: activities + all EvoLab
      endpoints + recovery snapshot, `missing=[]`, no 60s-timeout warnings.**

## Phase 5 — Cutover (~10 min, reversible in 10 s)

- [ ] Switch `baseURL` in the app (Profile) to the Oracle domain.
- [ ] Auto-deploy, hardened from day one:
      generate a **dedicated** deploy keypair; on the VM, add its public half
      to `authorized_keys` with a forced command so a leaked key can only
      trigger a deploy, never open a shell on a NOPASSWD-sudo account:
      `command="/home/ubuntu/phoenix/deploy/deploy.sh",no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA...`
      Capture the host key once from a trusted session
      (`ssh-keyscan -H <domain>`). Then set repo secrets `DEPLOY_HOST`,
      `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS` and variable
      `DEPLOY_ENABLED=true`. Verify with a trivial commit.
- [ ] Repoint the uptime monitor at the Oracle `/health` (keep pinging Render
      too until Phase 6 — it's the rollback).
- [ ] **GATE: the next real ~6am morning refresh completes clean on Oracle.**
      Watch it in `journalctl`. Rollback = flip `baseURL` back.

## Phase 6 — Decommission + upgrades (after 7 clean days)

- [ ] Suspend the Render service. Retire its keep-alive ping.
- [ ] Idle-reclaim guard: Oracle reclaims Always-Free VMs on low CPU
      utilization — pings don't count. Keep a small daily real-work task and
      watch for Oracle warning emails the first month.
- [ ] Flip `RENDER=true` off → real ChromaDB embeddings for chat RAG
      (verify `onnxruntime` has an aarch64 wheel first). One change, then a
      week of observation.
- [ ] Update `docs/DEPLOY.md` and CLAUDE.md (cold-start gotcha becomes
      obsolete; new deploy flow + rollback).

## Day-to-day after migration

- Deploy: push to `main` → GitHub Action SSHes in, pulls, restarts, checks
  `/health`. ~1 min.
- Manual: `ssh ubuntu@<domain>` → `./deploy.sh`. Rollback: `git revert` + push.
- Logs: `journalctl -u phoenix -f`.
- OS: unattended-upgrades is on; reboot roughly monthly.

## Known risks

| Risk | Answer |
|---|---|
| A1 "out of host capacity" | Different region at signup (home region is permanent — choose well) |
| In-VM iptables blocks 443 | Fixed by setup script; known Oracle gotcha |
| An aarch64 wheel missing | Playwright/FastAPI fine on ARM; only `onnxruntime` needs checking, and only at Phase 6 |
| SSE chat buffers behind proxy | `flush_interval -1` in Caddyfile; tested at Phase 4 |
| COROS throttles double logins | Never scrape both hosts in the same half hour |
| Oracle reclaims "idle" VM | Daily real-CPU task + watch emails; utilization, not pings, is what counts |
