"""Seed athletes.personal_model from an offline FIT-export fit.

The server refits from scraped activities every 7 days, but a thin scrape
history (< 20 steady runs) can't fit anything. This writes the coefficients
from the one-time FIT analysis (2026-09-09, 646 files) so the model is live
from day one. A later successful refit replaces it; a failed one keeps it.

Usage:
    PYTHONPATH=. ./venv/bin/python3 scripts/seed_personal_model.py            # local sqlite only
    PYTHONPATH=. ./venv/bin/python3 scripts/seed_personal_model.py --prod     # the .env DATABASE_URL

Without --prod this refuses any non-sqlite URL (CLAUDE.md rule).
"""
import argparse
import json
import os
import sys
from datetime import datetime

# Fitted 2026-09-09 on 76 steady outdoor runs, temperature from watch laps.
# HR = intercept + speed*(m/s) + temp*(°C) + ascent*(m per km). RMSE 5.7 bpm.
SEED = {
    "hr_model": {"intercept": 91.5, "speed": 17.6, "temp": 0.68, "ascent": 0.22,
                 "n": 76, "rmse": 5.7, "lthr": 185},
    "lthr": 185,
    "lthr_source": "fit_export",
    "source": "fit_export_2026-09-09",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod", action="store_true", help="allow the .env DATABASE_URL")
    args = ap.parse_args()

    if not args.prod:
        import dotenv
        dotenv.load_dotenv = lambda *a, **k: False
        os.environ.setdefault("DATABASE_URL", "sqlite:///phoenix_local.db")
        if not os.environ["DATABASE_URL"].startswith("sqlite"):
            sys.exit("Refusing non-sqlite DATABASE_URL without --prod")

    else:
        import dotenv
        dotenv.load_dotenv()

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.models.database import Athlete

    engine = create_engine(os.environ["DATABASE_URL"])
    print(f"target: {engine.url.render_as_string(hide_password=True)}")
    db = sessionmaker(bind=engine)()
    athlete = db.query(Athlete).first()
    if not athlete:
        sys.exit("no athlete row")
    now = datetime.utcnow().isoformat(timespec="seconds")
    athlete.personal_model = {**SEED, "fitted_at": now, "checked_at": now}
    db.commit()
    print("seeded:", json.dumps(athlete.personal_model))


if __name__ == "__main__":
    main()
