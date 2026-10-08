#!/usr/bin/env python3
"""COROS MCP CLI — bootstrap the token and poke the official server by hand.

The client itself lives in backend/services/coros_mcp.py (one client, used by
the morning refresh's shadow read). This script only drives it:

  ./venv/bin/python3 scripts/coros_mcp_cli.py login             # COROS_EMAIL/COROS_PASSWORD from .env
  ./venv/bin/python3 scripts/coros_mcp_cli.py login --browser   # prints a link, polls until you log in
  ./venv/bin/python3 scripts/coros_mcp_cli.py whoami            # token status, no secrets printed
  ./venv/bin/python3 scripts/coros_mcp_cli.py tools             # catalog → samples/coros_mcp/tools.json
  ./venv/bin/python3 scripts/coros_mcp_cli.py call --tool queryUserInfo --args '{}'
  ./venv/bin/python3 scripts/coros_mcp_cli.py gate [--days 7]   # parsed gate rows + missing fields
  ./venv/bin/python3 scripts/coros_mcp_cli.py pull [--days 10]  # the full scraper-shaped payload, no DB
  ./venv/bin/python3 scripts/coros_mcp_cli.py dry-run --plan week.json [--week-start 2026-03-09]
      # the courses Phoenix WOULD put on the watch, next to what the watch has; writes nothing.
      # --from-api https://host fetches GET /weekly-plan instead (note: that endpoint generates a
      # plan when the week has none, so prefer a saved file).

Read-only against COROS: create*/update*/schedule* tools are refused by the
client. Token: ~/.phoenix/coros_mcp/<region>/token.json (0600), outside the
repo. Raw outputs: samples/coros_mcp/ (gitignored — personal data). No DB.
Run `login` once on the VM: that is what switches the morning refresh to the MCP there.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from backend.services import coros_mcp as mcp  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "samples" / "coros_mcp"


def dump(name, obj):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / f"{name}.json"
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
    return p


def cmd_login(args):
    meta = mcp.discover()
    print(f"issuer: {meta['issuer']}  (region {mcp.region_of(meta['issuer'])})")
    client_id = mcp.register_client(meta)
    print(f"registered public client: {client_id[:6]}…")
    if args.browser:
        tok = mcp.browser_login(meta, client_id,
                                on_url=lambda u: print("Open this link and log into COROS:\n", u, flush=True))
    else:
        load_dotenv()  # only COROS_EMAIL / COROS_PASSWORD are read; no DB here
        email, password = os.getenv("COROS_EMAIL"), os.getenv("COROS_PASSWORD")
        if not email or not password:
            raise mcp.CorosMcpError("COROS_EMAIL / COROS_PASSWORD not set — or use --browser")
        tok = mcp.password_login(meta, client_id, email, password)
    p = mcp.save_token(tok)
    ttl = tok["expires_at"] - int(time.time())
    print(f"token saved to {p}  scope='{tok['scope']}'  expires in {ttl}s  "
          f"refresh_token={'yes' if tok.get('refresh_token') else 'NO'}")


def cmd_whoami(args):
    path = mcp.find_token_path()
    if not path:
        print("no token on this machine — run login"); return
    tok = mcp.load_token(path)
    ttl = tok.get("expires_at", 0) - int(time.time())
    print(f"{path}\nissuer {tok['issuer']}  client {tok['client_id'][:6]}…  scope '{tok.get('scope')}'\n"
          f"access token {'valid' if ttl > 0 else 'EXPIRED'} ({ttl}s)  "
          f"refresh token {'present' if tok.get('refresh_token') else 'absent'}  "
          f"obtained {time.strftime('%Y-%m-%d %H:%M', time.localtime(tok.get('obtained_at', 0)))}\n"
          f"MCP path enabled on this host: {mcp.enabled()}")


def cmd_tools(args):
    tools = mcp.McpClient(mcp.ensure_token()).list_tools()
    p = dump("tools", tools)
    print(f"{len(tools)} tools → {p}\n")
    for t in tools:
        props = (t.get("inputSchema") or {}).get("properties", {})
        req = (t.get("inputSchema") or {}).get("required", [])
        flag = "WRITE" if t["name"].lower().startswith(mcp.WRITE_PREFIXES) else "read "
        print(f"{flag} {t['name']:38s} ({', '.join(f'{k}{'*' if k in req else ''}' for k in props)})")


def cmd_call(args):
    arguments = json.loads(args.args or "{}")
    result = mcp.McpClient(mcp.ensure_token()).call(args.tool, arguments)
    p = dump(f"{args.tool}{'_' + args.suffix if args.suffix else ''}",
             {"arguments": arguments, "result": result})
    text = mcp.tool_text(result)
    print(f"== {args.tool}  isError={result.get('isError', False)}")
    print(text[:1500] + (" …" if len(text) > 1500 else ""))
    print(f"  → {p}")


def cmd_gate(args):
    fetched = mcp.fetch_gate_rows(days=args.days)
    rows = fetched["rows"]
    print(f"{len(rows)} days in {fetched['elapsed_ms']} ms over {fetched['calls']} calls")
    for d in sorted(rows, reverse=True):
        r = rows[d]
        missing = mcp.missing_gate_fields(r)
        print(f"{d}  hrv {r['hrv_ms']} (base {r['hrv_baseline']}, {r['hrv_eval']})  "
              f"rhr {r['resting_hr']}  ati/cti {r['ati']}/{r['cti']}  tib {r['tib']}  "
              f"fatigue {r['fatigue_state']}  ratio {r['load_ratio']} (state {r['load_ratio_state']})"
              + (f"  MISSING {missing}" if missing else ""))


def cmd_pull(args):
    """Run the live-sync fetch exactly as the refresh does, print the shape."""
    payload = mcp.fetch_scrape_shaped(days=args.days)
    p = dump("pull", payload)
    acts = payload["activities"]
    days = payload["evolab"]["analyse_query"]["dayList"]
    print(f"{payload['calls']} calls in {payload['elapsed_ms']} ms → {len(acts)} activities, {len(days)} day rows; "
          f"today {payload['today_status']} missing={payload['missing']}")
    for a in sorted(acts, key=lambda a: a["startTimeLocal"], reverse=True)[:8]:
        print(f"  {a['startTimeLocal']}  type {a['sportType']:4d}  {a['distance']/1000:6.2f} km  {a['duration']//60:4d} min  "
              f"HR {a['avgHeartRate']}  TL {a['trainingLoad']}  {a['name']}")
    today = max(days, key=lambda d: d["happenDay"]) if days else {}
    print("  latest day row:", {k: v for k, v in today.items()})
    print(f"  → {p}")


def cmd_dry_run(args):
    """Map this week's plan to courses and show the watch's calendar beside it.
    No write tool is ever called here."""
    from datetime import date, timedelta
    from backend.services import coros_watch as cw

    if args.plan:
        plan = json.loads(Path(args.plan).read_text())
    elif args.from_api:
        import requests
        plan = requests.get(f"{args.from_api.rstrip('/')}/weekly-plan", timeout=60).json()
    else:
        raise mcp.CorosMcpError("dry-run needs --plan FILE or --from-api URL")
    plan = plan.get("plan") if isinstance(plan, dict) and "plan" in plan and "days" not in plan else plan
    today = date.today()
    week_start = date.fromisoformat(args.week_start) if args.week_start else today - timedelta(days=today.weekday())
    week = cw.plan_day_courses(plan, week_start)

    schedule = {}
    try:
        text = mcp.McpClient(mcp.ensure_token()).call_text("queryTrainingSchedule", {
            "startDate": week_start.strftime("%Y%m%d"),
            "endDate": (week_start + timedelta(days=6)).strftime("%Y%m%d")})
        schedule = cw.parse_training_schedule(text)
        print(f"watch calendar read for {week_start} → {len(schedule)} days with items")
    except mcp.CorosMcpError as e:
        print(f"watch calendar not read ({e}); showing the plan side only")

    from backend.services import coros_watch_sync as ws
    decisions = {}
    if schedule or week:
        dates = [d for d in sorted(week) if d >= today]
        for a in ws.decide_actions(week, schedule, {}, dates):
            decisions.setdefault(a.date, []).append(a)
    print(f"\nwould write: {sum(1 for acts in decisions.values() for a in acts if a.kind != 'skip')} "
          f"(flag COROS_WATCH_PUSH={os.getenv('COROS_WATCH_PUSH', 'unset')}; this command writes nothing)")
    for d in sorted(week):
        dc = week[d]
        print(f"\n{d} {d.strftime('%A')}" + (f"   [stripped: {dc.enforced_reason}]" if dc.enforced_reason else ""))
        for a in decisions.get(d, []):
            print(f"   decide slot {a.slot}: {a.kind.upper()} — {a.reason}" + (f" (idInPlan {a.id_in_plan})" if a.id_in_plan else ""))
        for slot, course in dc.courses:
            def _int(sec):
                if "sectionIntensity" in sec:
                    return f"Z{sec['sectionIntensity']}"
                return f"{sec.get('intensityValueStart')}-{sec.get('intensityValueEnd')}bpm"
            secs = " · ".join(f"{s['sectionType']}:{s.get('targetValue', 'free')}s {_int(s)}" for s in course["sections"])
            print(f"   plan  slot {slot}: {course['courseName']}  [{secs}]  hash {cw.course_hash(course)}")
        for err in dc.errors:
            print(f"   plan  REFUSED: {err}")
        for it in schedule.get(d, []):
            tag = f"Phoenix slot {it['phoenix_slot']}" if it.get("phoenix_slot") else "athlete"
            print(f"   watch {tag}: {it['name']}  idInPlan {it['idInPlan']}" + ("  (completed)" if it.get("completed") else ""))
        if not dc.courses and not dc.errors and not schedule.get(d):
            print("   —")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("login"); s.add_argument("--browser", action="store_true"); s.set_defaults(fn=cmd_login)
    s = sub.add_parser("whoami"); s.set_defaults(fn=cmd_whoami)
    s = sub.add_parser("tools"); s.set_defaults(fn=cmd_tools)
    s = sub.add_parser("call"); s.add_argument("--tool", required=True); s.add_argument("--args", default="{}")
    s.add_argument("--suffix", default=""); s.set_defaults(fn=cmd_call)
    s = sub.add_parser("gate"); s.add_argument("--days", type=int, default=7); s.set_defaults(fn=cmd_gate)
    s = sub.add_parser("pull"); s.add_argument("--days", type=int, default=10); s.set_defaults(fn=cmd_pull)
    s = sub.add_parser("dry-run"); s.add_argument("--plan"); s.add_argument("--from-api"); s.add_argument("--week-start")
    s.set_defaults(fn=cmd_dry_run)
    args = ap.parse_args()
    try:
        args.fn(args)
    except mcp.CorosMcpError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
