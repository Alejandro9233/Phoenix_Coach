#!/usr/bin/env bash
# Phoenix Coach — one-shot server setup for the Oracle Always-Free VM
# (Ubuntu 24.04 aarch64). Phase 2 of docs/MIGRATE_ORACLE.md.
#
#   Run as the default `ubuntu` user:   bash deploy/setup_server.sh
#   Phase 3 (re-run with):              DOMAIN=phoenix-coach.duckdns.org DUCKDNS_TOKEN=xxx bash deploy/setup_server.sh
#
# Idempotent: safe to re-run after a failure or to apply the Phase 3 env vars.
# It does NOT start the app — the .env must be scp'd in first (see the
# checklist this script prints at the end).
set -euo pipefail

APP_USER=ubuntu
APP_DIR=/home/$APP_USER/phoenix
REPO_URL=${REPO_URL:-https://github.com/Alejandro9233/Phoenix_Coach.git}
DOMAIN=${DOMAIN:-}
DUCKDNS_TOKEN=${DUCKDNS_TOKEN:-}

[ "$(id -un)" = "$APP_USER" ] || { echo "Run this as the $APP_USER user."; exit 1; }

echo "==> apt packages"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git python3-venv python3-pip curl ca-certificates gnupg \
  unattended-upgrades iptables-persistent

echo "==> unattended security upgrades"
sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

echo "==> 2G swap file (safety net, not a crutch)"
if ! sudo swapon --show | grep -q '^/swapfile'; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
fi
grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null

echo "==> open 80/443 in the VM's own iptables (Oracle images REJECT by default;"
echo "    the cloud security list alone is NOT enough — known gotcha)"
for port in 80 443; do
  if ! sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT 1 -p tcp --dport "$port" -j ACCEPT
  fi
done
sudo netfilter-persistent save
# 8001 stays closed on purpose: only Caddy (on this box) talks to the app.

echo "==> clone/update the repo"
if [ ! -d "$APP_DIR/.git" ]; then
  git clone "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" pull --ff-only
fi
cd "$APP_DIR"

echo "==> python venv + pinned deps"
[ -d venv ] || python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

echo "==> playwright chromium (browsers inside the venv: PLAYWRIGHT_BROWSERS_PATH=0,"
echo "    same as the .env — install location and runtime lookup must agree)"
sudo "$APP_DIR/venv/bin/playwright" install-deps chromium
PLAYWRIGHT_BROWSERS_PATH=0 venv/bin/playwright install chromium

echo "==> systemd service (installed + enabled, NOT started — .env first)"
sudo cp deploy/phoenix.service /etc/systemd/system/phoenix.service
sudo systemctl daemon-reload
sudo systemctl enable phoenix

# DuckDNS before Caddy: the A record must exist before Caddy's first ACME
# attempt, or the initial cert request fails on NXDOMAIN (it self-heals via
# retries, but there's no reason to start in a hole).
if [ -n "$DOMAIN" ] && [ -n "$DUCKDNS_TOKEN" ]; then
  echo "==> DuckDNS updater (every 10 min)"
  SUBDOMAIN=${DOMAIN%%.*}
  sudo install -m 600 -o root -g root /dev/null /etc/duckdns.env
  sudo tee /etc/duckdns.env >/dev/null <<EOF
DUCKDNS_SUBDOMAIN=$SUBDOMAIN
DUCKDNS_TOKEN=$DUCKDNS_TOKEN
EOF
  sudo tee /etc/systemd/system/duckdns.service >/dev/null <<'EOF'
[Unit]
Description=DuckDNS IP updater

[Service]
Type=oneshot
EnvironmentFile=/etc/duckdns.env
# DuckDNS answers HTTP 200 with body "KO" on a bad token — grep makes that
# a visible unit failure instead of a silently stale record.
ExecStart=/bin/sh -c 'curl -fsS "https://www.duckdns.org/update?domains=${DUCKDNS_SUBDOMAIN}&token=${DUCKDNS_TOKEN}&ip=" | grep -q OK'
EOF
  sudo tee /etc/systemd/system/duckdns.timer >/dev/null <<'EOF'
[Unit]
Description=Run the DuckDNS updater every 10 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=10min

[Install]
WantedBy=timers.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now duckdns.timer
  echo "    first update, synchronous:"
  sudo systemctl start duckdns.service
fi

echo "==> Caddy"
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y caddy
fi
if [ -n "$DOMAIN" ]; then
  sed "s/__DOMAIN__/$DOMAIN/" deploy/Caddyfile | sudo tee /etc/caddy/Caddyfile >/dev/null
  sudo systemctl reload caddy || sudo systemctl restart caddy
  echo "    Caddy serving https://$DOMAIN (first cert may take a minute)"
else
  echo "    (no DOMAIN set — Caddy config deferred to Phase 3)"
fi

cat <<'EOF'

========================================================================
Setup done. Next (Phase 2 checklist in docs/MIGRATE_ORACLE.md):

  1. Build the VM .env — do NOT scp the local one blindly:
       keep from local .env:  DATABASE_URL (Supabase), COROS_EMAIL,
                              COROS_PASSWORD, GROQ_API_KEY
       add from the Render dashboard:  COACHING_MODEL (the local .env
                              pins a RETIRED model — use Render's value),
                              RENDER=true, PLAYWRIGHT_BROWSERS_PATH=0
       never set:             TIMEZONE (breaks travel), OLLAMA_MODEL,
                              SCRAPER_DEBUG_DIR
     Then:   scp <that file> ubuntu@<IP>:/home/ubuntu/phoenix/.env
  2. chmod 600 /home/ubuntu/phoenix/.env
  3. sudo systemctl start phoenix
  4. curl -fsS localhost:8001/health          -> expect 200
  5. GATE: cd /home/ubuntu/phoenix &&
     PYTHONPATH=. venv/bin/python3 scripts/scraper_health_check.py
     ARM Chromium against real COROS must pass with missing=[]
     before anything else happens.
========================================================================
EOF
