"""Rebuild the LOCAL sqlite database from FIT files + the last scrape dump.

This script is the manual-fallback path for activity history: if the
scraper's backfill can't work, export FIT files from COROS activity detail
pages into fit_examples/ and rerun.

It targets ONLY the local phoenix_coach.db, by construction: the db_url is
pinned to sqlite below and .env is never consulted. IngestionService() with
no argument would silently resolve .env's DATABASE_URL — the production
Postgres — which is exactly the accident the CLAUDE.md dotenv rule exists to
prevent in a script that starts by deleting a database file.
"""
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from backend.services.ingestion_service import IngestionService

DB_FILE = "phoenix_coach.db"
SCRAPE_DUMP = "coros_scrape_result.json"


def rebuild():
    if os.path.exists(DB_FILE):
        print(f"Deleting existing database: {DB_FILE}")
        os.remove(DB_FILE)

    # Pinned to the local file — never the env DATABASE_URL.
    service = IngestionService(db_url=f"sqlite:///./{DB_FILE}")

    # 1. Bulk Import FIT activities first (often more historical).
    print("Step 1: Bulk importing FIT files from fit_examples...")
    service.bulk_import_fit_directory("fit_examples")

    # 2. Ingest the last COROS scrape dump (EvoLab history + recent activities)
    if os.path.exists(SCRAPE_DUMP):
        print(f"Step 2: Ingesting COROS scrape data from {SCRAPE_DUMP}...")
        service.ingest_coros_data(SCRAPE_DUMP)
    else:
        print(f"Step 2 skipped: no {SCRAPE_DUMP} in the repo root. "
              "Run the scraper (or a smart refresh) to produce one.")

    print("Database rebuild complete!")


if __name__ == "__main__":
    rebuild()
