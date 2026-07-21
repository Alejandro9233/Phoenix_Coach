"""
One-time migration: dump SQLite data → PostgreSQL.

Usage:
    POSTGRES_URL="postgresql://..." python scripts/migrate_sqlite_to_postgres.py

Reads from local phoenix_coach.db, writes to the Postgres instance.
"""
import os
import sys

# Ensure backend modules can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=True)

from sqlalchemy import create_engine, MetaData

SQLITE_URL = "sqlite:///./phoenix_coach.db"
POSTGRES_URL = os.environ.get("POSTGRES_URL")

if not POSTGRES_URL:
    print("Error: POSTGRES_URL environment variable is not set.")
    print("Usage: POSTGRES_URL=\"postgresql://...\" python scripts/migrate_sqlite_to_postgres.py")
    sys.exit(1)

from backend.models.database import Base

sqlite_engine = create_engine(SQLITE_URL)
pg_engine = create_engine(POSTGRES_URL)

# Create tables in Postgres using our cross-db declarative models
print("Creating tables in PostgreSQL...")
Base.metadata.create_all(bind=pg_engine)

# Copy data table by table
print("Starting data migration...")
with sqlite_engine.connect() as src, pg_engine.connect() as dst:
    for table in Base.metadata.sorted_tables:
        rows = src.execute(table.select()).fetchall()
        if rows:
            # Convert to list of dicts
            cols = [c.name for c in table.columns]
            data = [dict(zip(cols, row)) for row in rows]
            dst.execute(table.insert(), data)
            dst.commit()
            print(f"  ✅ Migrated {len(data)} rows → {table.name}")
        else:
            print(f"  ⏭️  {table.name}: empty, skipped")

print("Migration complete!")
