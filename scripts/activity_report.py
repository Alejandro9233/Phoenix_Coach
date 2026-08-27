"""Print a per-block workout report for a COROS activity.

Usage:
    # offline, from a saved detail payload
    PYTHONPATH=. ./venv/bin/python3 scripts/activity_report.py --file samples/tempo-run.json

    # live: fetch by labelId (one read-only request), then report
    PYTHONPATH=. ./venv/bin/python3 scripts/activity_report.py 479886220944506880
    PYTHONPATH=. ./venv/bin/python3 scripts/activity_report.py <labelId> --sport 100
    PYTHONPATH=. ./venv/bin/python3 scripts/activity_report.py <labelId> --save samples/run.json

labelIds show in the site's activity URLs (?labelId=...) and in the rows the
daily scrape stores.
"""

import argparse
import json
import sys

from backend.services.activity_blocks import decode_activity, render_report


def main():
    parser = argparse.ArgumentParser(
        description="Block-by-block COROS workout report")
    parser.add_argument("label_id", nargs="?", help="activity labelId to fetch")
    parser.add_argument("--file", help="saved detail JSON (skips the fetch)")
    parser.add_argument("--sport", type=int, default=100,
                        help="sportType for the fetch (default 100 = run)")
    parser.add_argument("--save", help="also write the fetched payload here")
    args = parser.parse_args()

    if bool(args.file) == bool(args.label_id):
        parser.error("give exactly one of: a labelId, or --file")
    if args.label_id and not args.label_id.isdigit():
        parser.error("labelId must be numeric (paste the digits, not the URL)")
    if args.file and args.save:
        parser.error("--save only applies when fetching by labelId")

    if args.file:
        with open(args.file) as f:
            payload = json.load(f)
    else:
        from backend.services.coros_activity_detail import (
            fetch_activity_detail_sync)
        payload = fetch_activity_detail_sync(args.label_id, args.sport)
        if args.save:
            with open(args.save, "w") as f:
                json.dump(payload, f)
            print(f"(payload saved to {args.save})\n", file=sys.stderr)

    print(render_report(decode_activity(payload)))


if __name__ == "__main__":
    main()
