import os
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from backend.services.ingestion_service import IngestionService
from dotenv import load_dotenv

load_dotenv()

def rebuild():
    db_file = "phoenix_coach.db"
    if os.path.exists(db_file):
        print(f"Deleting existing database: {db_file}")
        os.remove(db_file)
    
    service = IngestionService()
    
    # 1. Bulk Import FIT activities first (often more historical)
    print("Step 1: Bulk importing FIT files from Bulk_activities...")
    service.bulk_import_fit_directory("Bulk_activities")
    
    # 2. Ingest COROS Scrape Data (EvoLab history + recent activities)
    print("Step 2: Ingesting COROS scrape data...")
    service.ingest_coros_data("coros_scraped_data.json")
    
    print("Database rebuild complete!")

if __name__ == "__main__":
    rebuild()
