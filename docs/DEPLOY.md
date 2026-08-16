# Deploying Phoenix Coach

Backend runs on Render at `https://phoenix-coach.onrender.com`.
Most of this exists **only in the Render dashboard** — this file is the offline record.

## Environment variables (Render dashboard)

```bash
DATABASE_URL=postgresql://...        # auto-provisioned by Render
GROQ_API_KEY=gsk_...                 # console.groq.com/keys
COACHING_MODEL=llama-3.3-70b-versatile
RENDER=true                          # triggers memory-safe RAG mode (see below)
COROS_EMAIL=...
COROS_PASSWORD=...
PYTHONPATH=.
PLAYWRIGHT_BROWSERS_PATH=0           # persists the Chromium cache between builds
```

**`TIMEZONE` is deliberately NOT set.** It overrides the timezone the phone reports and
breaks travel. The app sends `TimeZone.current.identifier` on every profile save.

`OLLAMA_MODEL` and `SCRAPER_DEBUG_DIR` are local-dev only — don't set them on Render.

## Build and start

- **Build**: `pip install -r requirements.txt && playwright install chromium --with-deps`
- **Start** (`Procfile`): `web: uvicorn backend.main:app --host 0.0.0.0 --port $PORT`

## Free-tier constraints

| Limit | Consequence |
|---|---|
| Spins down after 15 min idle | First request takes 30-60s. iOS covers it with a 180s timeout and cached fallbacks. **This is expected — don't "fix" it.** |
| 512 MB RAM | `knowledge_base.py` checks `RENDER == "true"` and disables ChromaDB ONNX embeddings, falling back to keyword search. Loading real embeddings in prod will OOM the container. |
| Groq: 30 req/min, 100K tok/day | Normal usage (~10-20 calls/day) is fine. A regenerate loop will blow through it. |

## Schema changes

There is **no migration framework**. `main.py` runs lightweight `ALTER TABLE` statements
on startup — that's how columns like `athletes.timezone` reached production. Add new
columns the same way, and make them nullable.

## Rollback

- `llm_client.py` auto-falls back to Ollama (`qwen3:8b`) when `GROQ_API_KEY` is unset.
- The iOS backend URL is editable in Profile settings (persisted in UserDefaults);
  `resetToDefaultURL()` clears the cache when the compiled default changes.
- There is no local SQLite snapshot in the repo — use `scripts/rebuild_db.py` to build one.

## Cost

$0/month — Groq free tier + Render free web service + free PostgreSQL.
