import asyncio
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from backend.services.coros_scraper import CorosScraper

async def main():
    scraper = CorosScraper()
    print("Starting full scrape... this may take 30-60 seconds.")
    data = await scraper.scrape_all()
    
    # Save to JSON for inspection
    output_file = "coros_scraped_data.json"
    with open(output_file, "w") as f:
        json.dump(data, f, indent=2)
        
    print(f"\nScrape complete! Data saved to {output_file}")
    print(f"Captured {len(data.get('activities', []))} activities.")
    print(f"Captured health keys: {list(data.get('health', {}).keys())}")
    print(f"Captured evolab keys: {list(data.get('evolab', {}).keys())}")

if __name__ == "__main__":
    asyncio.run(main())
