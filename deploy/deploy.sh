#!/usr/bin/env bash
# Phoenix Coach — deploy the latest main on the Oracle VM.
# Run on the VM (the GitHub Action does exactly this over SSH):
#   ~/phoenix/deploy/deploy.sh
set -euo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"

# The VM checkout is not a workspace: converge on origin/main, never merge.
# (--ff-only would dead-end forever after a force-push or a stray local edit.)
git fetch origin main
git reset --hard origin/main
echo "==> at $(git log -1 --format='%h %s')"

# Unconditional on purpose: with exact pins this is a fast no-op when
# satisfied, and a conditional gate ratchets past a failed install (pull
# succeeds, pip fails, next run sees no diff and boots new code on old deps).
# requirements.lock (full transitive freeze, generated on this VM at P2)
# wins over the human-edited top-level list when present.
if [ -f requirements.lock ]; then
  venv/bin/pip install -r requirements.lock
else
  venv/bin/pip install -r requirements.txt
fi

# pip upgrade of playwright deletes the browsers stored inside the package
# (PLAYWRIGHT_BROWSERS_PATH=0) — this restores them; no-op when present.
PLAYWRIGHT_BROWSERS_PATH=0 venv/bin/playwright install chromium

sudo systemctl restart phoenix

# Budget matches worst-case startup: import-time migrations + a paused
# Supabase can take minutes. Wall-clock bound, not iteration count.
echo "==> waiting for /health (up to 300s)"
SECONDS=0
while [ "$SECONDS" -lt 300 ]; do
  if curl -fsS -m 5 http://127.0.0.1:8001/health >/dev/null 2>&1; then
    echo "==> healthy after ${SECONDS}s at $(git log -1 --format='%h %s')"
    exit 0
  fi
  sleep 2
done

echo "!! /health not answering after ${SECONDS}s" >&2
echo "!! unit state: $(systemctl is-active phoenix)" >&2
journalctl -u phoenix -n 20 --no-pager >&2 || true
exit 1
