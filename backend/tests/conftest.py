"""Keep the test suite off the production database — structurally.

backend/main.py runs `load_dotenv(override=True)` and then `create_all(engine)`
at import, so merely importing it on a machine holding prod credentials opens a
connection to the production Postgres (CLAUDE.md rule). The get_db override in
each test file protects request handlers, but never protected that import-time
connect — which went unnoticed until 2026-08-20, when the prod DB expired and
the whole suite failed at collection.

pytest imports this conftest before any test module, so the patch lands before
backend.main can read .env. Belt and suspenders: neutralize load_dotenv AND
pin DATABASE_URL to in-memory sqlite.
"""
import os

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False
os.environ["DATABASE_URL"] = "sqlite://"

assert os.environ["DATABASE_URL"].startswith("sqlite"), (
    "Tests must never see a non-sqlite DATABASE_URL"
)
