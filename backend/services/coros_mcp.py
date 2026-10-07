"""
COROS MCP — the official COROS data path. Read-only, shadow mode.

COROS ships an MCP server (https://mcp.coros.com/mcp, repo coroslab/COROS-MCP)
that returns the same recovery numbers the Playwright scraper sniffs, over
OAuth 2.0 instead of a headless Chromium login. The spike and field map live
in docs/COROS_MCP.md; the council verdict (2026-10-07) that shaped this file:

- **Shadow only.** This module fetches, parses and COMPARES. It never writes
  `recovery_snapshots` or `activities`. Ingestion's dedupe would turn a second
  writer into a silent no-op and hide the very diff we need, and last-write-
  wins would make the morning gates depend on run order. The diff rides the
  refresh event (`mcp_shadow`) so parity is a record, not a feeling.
- **None, never 0.** Every tool answers in formatted prose, not JSON. A line
  COROS rewords must come back as None and show up in `missing`, not slide
  through ingestion's `.get(x, 0)` defaults as "all clear".
- **Derivations, verified on 84 scraper days:** tib = cti − ati (exact),
  fatigue_pct = −tib (exact), load_ratio_state = fixed zones (exact),
  fatigue_state = zone of −tib at (−cti/2, −0.1·cti, 0.05·cti, 0.8·cti) —
  82/84, two boundary days unexplained; cutover waits on those.
  Recommended weekly load band = 7 × previous Sunday's cti, max = 1.8 × min.
- **Write tools are refused here.** Pushing workouts to the watch is a
  separate council decision; `WRITE_PREFIXES` makes that explicit in code.
- The scraper stays the live path until Alex lifts the hold. Nothing in this
  module is reachable from the refresh unless a token file exists and
  COROS_MCP_SHADOW is not "0".

Token: ~/.phoenix/coros_mcp/<region>/token.json (0600), never in the repo,
never in the DB, never printed. One-time bootstrap on the VM:
`./venv/bin/python3 scripts/coros_mcp_cli.py login`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import stat
import time
import urllib.parse
from datetime import date
from pathlib import Path

import requests

GATEWAY = "https://mcp.coros.com"
SCOPES = "openid offline_access mcp.tools"
CLIENT_NAME = "Phoenix Coach"
REDIRECT_URI = "http://127.0.0.1:43123/callback"
PROTOCOL_VERSION = "2025-06-18"
WRITE_PREFIXES = ("create", "update", "schedule", "delete", "remove")
HTTP_TIMEOUT = (10, 25)  # connect, read — the refresh caller bounds the whole run too

# The eight recovery_snapshots columns the adaptation gates and the engine
# read. If any is None for today, the shadow report says so.
GATE_FIELDS = ("hrv_ms", "hrv_baseline", "resting_hr", "ati", "cti",
               "tib", "fatigue_state", "load_ratio")
COMPARE_FIELDS = GATE_FIELDS + ("fatigue_pct", "load_ratio_state")
# Scraper stores floats that COROS rounds in prose; ints must match exactly.
TOLERANCE = {"load_ratio": 0.011, "tib": 0.51, "fatigue_pct": 0.51,
             "ati": 0.51, "cti": 0.51}


class CorosMcpError(Exception):
    pass


# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------

def token_root() -> Path:
    return Path(os.getenv("COROS_MCP_TOKEN_ROOT") or (Path.home() / ".phoenix" / "coros_mcp"))


def find_token_path() -> Path | None:
    """Explicit COROS_MCP_TOKEN_PATH wins; else the newest <region>/token.json."""
    explicit = os.getenv("COROS_MCP_TOKEN_PATH")
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    candidates = sorted(token_root().glob("*/token.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def region_of(issuer: str) -> str:
    host = urllib.parse.urlparse(issuer).hostname or "unknown"
    return {"mcpcn.coros.com": "cn", "mcpeu.coros.com": "eu",
            "mcpus.coros.com": "us"}.get(host, host.replace(".", "-"))


def load_token(path: Path) -> dict:
    tok = json.loads(path.read_text())
    for key in ("issuer", "client_id", "access_token"):
        if not tok.get(key):
            raise CorosMcpError(f"token file {path} is missing {key}")
    return tok


def save_token(tok: dict) -> Path:
    explicit = os.getenv("COROS_MCP_TOKEN_PATH")
    path = Path(explicit) if explicit else token_root() / region_of(tok["issuer"]) / "token.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tok, indent=2))
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path


def _safe_error_body(text: str) -> str:
    """Error bodies can echo tokens; keep only the error fields."""
    try:
        d = json.loads(text)
        return json.dumps({k: v for k, v in d.items()
                           if k in ("error", "error_description", "code", "message")})
    except Exception:
        return text[:120]


def _token_set(payload: dict, client_id: str, issuer: str) -> dict:
    if not payload.get("access_token"):
        raise CorosMcpError("token response has no access_token")
    now = int(time.time())
    return {
        "issuer": issuer,
        "client_id": client_id,
        "token_type": payload.get("token_type", "Bearer"),
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token"),
        "scope": payload.get("scope", SCOPES),
        "expires_at": now + int(payload.get("expires_in", 3600)),
        "obtained_at": now,
    }


def refresh_token(tok: dict) -> dict:
    if not tok.get("refresh_token"):
        raise CorosMcpError("access token expired and no refresh token — run login again")
    r = requests.post(f"{tok['issuer']}/oauth2/token", data={
        "grant_type": "refresh_token",
        "client_id": tok["client_id"],
        "refresh_token": tok["refresh_token"],
    }, timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        raise CorosMcpError(f"token refresh failed: HTTP {r.status_code} "
                            f"{_safe_error_body(r.text)} — run login again")
    new = _token_set(r.json(), tok["client_id"], tok["issuer"])
    # Some servers rotate refresh tokens, some don't echo them back. Keep the
    # old one when the response omits it.
    new["refresh_token"] = new["refresh_token"] or tok.get("refresh_token")
    return new


def ensure_token(skew_s: int = 60) -> dict:
    """Load the saved token, refreshing it when within `skew_s` of expiry."""
    path = find_token_path()
    if path is None:
        raise CorosMcpError("no COROS MCP token on this machine — run "
                            "`scripts/coros_mcp_cli.py login`")
    tok = load_token(path)
    if tok.get("expires_at", 0) - skew_s > time.time():
        return tok
    tok = refresh_token(tok)
    save_token(tok)
    return tok


# --------------------------------------------------------------------------
# OAuth login (one-time bootstrap; the CLI drives these)
# --------------------------------------------------------------------------

def discover(issuer: str = GATEWAY) -> dict:
    """The gateway answers with the regional issuer's OpenID metadata."""
    r = requests.get(f"{issuer}/.well-known/openid-configuration", timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    meta = r.json()
    if not str(meta.get("issuer", "")).startswith("https://"):
        raise CorosMcpError("discovery returned no issuer")
    return meta


def register_client(meta: dict) -> str:
    r = requests.post(meta["registration_endpoint"], json={
        "client_name": CLIENT_NAME,
        "redirect_uris": [REDIRECT_URI],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": SCOPES,
        "token_endpoint_auth_method": "none",
    }, timeout=HTTP_TIMEOUT)
    if r.status_code not in (200, 201) or not r.json().get("client_id"):
        raise CorosMcpError(f"client registration failed: HTTP {r.status_code}")
    return r.json()["client_id"]


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _authorize_url(meta: dict, client_id: str, challenge: str, state: str) -> str:
    return meta["authorization_endpoint"] + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id,
        "redirect_uri": REDIRECT_URI, "scope": SCOPES,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "resource": f"{meta['issuer']}/mcp", "state": state,
    })


def _code_from_callback(url: str, expected_state: str) -> str:
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    code, state = q.get("code", [""])[0], q.get("state", [""])[0]
    if not code:
        raise CorosMcpError("callback carries no code")
    if state != expected_state:
        raise CorosMcpError("state mismatch on callback")
    return code


def _hop(session, method, url, **kw):
    r = session.request(method, url, allow_redirects=False, timeout=HTTP_TIMEOUT, **kw)
    return r, r.headers.get("Location")


def exchange_code(meta: dict, client_id: str, code: str, verifier: str) -> dict:
    r = requests.post(meta["token_endpoint"], data={
        "grant_type": "authorization_code", "client_id": client_id,
        "code": code, "redirect_uri": REDIRECT_URI, "code_verifier": verifier,
    }, timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        raise CorosMcpError(f"token exchange failed: HTTP {r.status_code} {_safe_error_body(r.text)}")
    return _token_set(r.json(), client_id, meta["issuer"])


def password_login(meta: dict, client_id: str, email: str, password: str) -> dict:
    """COROS's own headless path (their skill helper's `--legacy` flow):
    authorize → login form → redirect chain → code. Same credentials the
    scraper uses; verified live 2026-10-07."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    s = requests.Session()
    r, loc = _hop(s, "GET", _authorize_url(meta, client_id, challenge, state))
    if r.status_code not in (302, 303) or not loc:
        raise CorosMcpError(f"authorize did not redirect to the COROS login (HTTP {r.status_code})")
    if loc.startswith(REDIRECT_URI):
        return exchange_code(meta, client_id, _code_from_callback(loc, state), verifier)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
    form = {
        "client_id": q.get("client_id", [""])[0],
        "redirect_uri": q.get("redirect_uri", [""])[0],
        "state": q.get("state", [""])[0],
        "scope": q.get("scope", [""])[0],
        "response_type": q.get("response_type", ["code"])[0],
        "activityType": "", "language": "en", "country": "US",
        "userName": email, "password": password,
        "checkStatus": "1", "getAllHistoryIn24Hours": "0",
    }
    r, loc = _hop(s, "POST", loc, data=form)
    if r.status_code not in (302, 303) or not loc:
        raise CorosMcpError(f"COROS login did not continue (HTTP {r.status_code})")
    for _ in range(6):
        if loc.startswith(REDIRECT_URI):
            return exchange_code(meta, client_id, _code_from_callback(loc, state), verifier)
        r, loc = _hop(s, "GET", loc)
        if r.status_code not in (302, 303) or not loc:
            raise CorosMcpError(f"authorization chain stalled at HTTP {r.status_code}")
    raise CorosMcpError("too many redirect hops without reaching the client callback")


def browser_login(meta: dict, client_id: str, on_url, timeout_s: int = 300) -> dict:
    """COROS CLI-session flow: hand the human a link, poll until they log in."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    r = requests.post(f"{meta['issuer']}/api/v1/cli/login-sessions",
                      json={"clientId": client_id}, timeout=HTTP_TIMEOUT)
    if r.status_code not in (200, 201):
        raise CorosMcpError(f"cli session failed: HTTP {r.status_code}")
    sess = r.json()
    on_url(sess["loginUrl"])
    interval = max(1, int(sess.get("intervalSeconds") or 3))
    deadline = time.monotonic() + timeout_s
    while True:
        c = requests.post(f"{meta['issuer']}/api/v1/cli/login-sessions/{sess['sessionId']}/claim",
                          headers={"X-Poll-Token": sess["pollToken"]}, timeout=HTTP_TIMEOUT)
        payload = c.json() if c.content else {}
        status = str(payload.get("status", "")).lower()
        if status == "authorized":
            ticket = payload["loginTicket"]
            break
        if status != "pending":
            raise CorosMcpError(f"cli session ended: {status or c.status_code}")
        if time.monotonic() > deadline:
            raise CorosMcpError("timed out waiting for the browser login")
        time.sleep(interval)
    parsed = urllib.parse.urlparse(_authorize_url(meta, client_id, challenge, state))
    qs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) + [("login_ticket", ticket)]
    r, loc = _hop(requests.Session(), "GET",
                  urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(qs))))
    if r.status_code not in (302, 303) or not loc:
        raise CorosMcpError(f"ticketed authorize did not redirect (HTTP {r.status_code})")
    return exchange_code(meta, client_id, _code_from_callback(loc, state), verifier)


# --------------------------------------------------------------------------
# MCP transport
# --------------------------------------------------------------------------

def tool_text(result: dict) -> str:
    """Flatten a tools/call result to text. COROS wraps prose as a JSON string
    literal inside the text block; lap data comes as a JSON object."""
    if result.get("isError"):
        raise CorosMcpError(f"tool returned isError: {str(result.get('content'))[:200]}")
    parts = []
    for block in result.get("content") or []:
        if block.get("type") != "text":
            continue
        text = block.get("text", "")
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, str):
            parts.append(parsed)
        elif isinstance(parsed, (dict, list)):
            parts.append(json.dumps(parsed, ensure_ascii=False))
        else:
            parts.append(text)
    return "\n".join(parts)


class McpClient:
    """Stateless streamable-HTTP MCP: initialize once per client, then
    tools/list and tools/call. No session id, no notifications/initialized."""

    def __init__(self, token: dict):
        self.url = f"{token['issuer']}/mcp"
        self._headers = {
            "Authorization": f"{token.get('token_type', 'Bearer')} {token['access_token']}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        self._next_id = 1
        self._initialized = False
        self.calls = 0

    def _rpc(self, method: str, params: dict) -> dict:
        rid = self._next_id
        self._next_id += 1
        self.calls += 1
        r = requests.post(self.url, headers=self._headers, timeout=HTTP_TIMEOUT,
                          json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        if r.status_code != 200:
            raise CorosMcpError(f"{method}: HTTP {r.status_code} {_safe_error_body(r.text)}")
        payload = None
        if "text/event-stream" in r.headers.get("content-type", ""):
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    try:
                        candidate = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if candidate.get("id") == rid:
                        payload = candidate
            if payload is None:
                raise CorosMcpError(f"{method}: SSE stream had no response for id {rid}")
        else:
            payload = r.json()
        if "error" in payload:
            raise CorosMcpError(f"{method}: {json.dumps(payload['error'])[:300]}")
        return payload.get("result", {})

    def _ensure_initialized(self):
        if not self._initialized:
            self._rpc("initialize", {
                "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": "shadow"},
            })
            self._initialized = True

    def list_tools(self) -> list[dict]:
        self._ensure_initialized()
        tools, cursor = [], None
        while True:
            res = self._rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(res.get("tools", []))
            cursor = res.get("nextCursor")
            if not cursor:
                return tools

    def call(self, name: str, arguments: dict | None = None) -> dict:
        if name.lower().startswith(WRITE_PREFIXES):
            raise CorosMcpError(f"refusing {name}: the write path is a separate decision "
                                "(docs/COROS_MCP.md); this client is read-only")
        self._ensure_initialized()
        return self._rpc("tools/call", {"name": name, "arguments": arguments or {}})

    def call_text(self, name: str, arguments: dict | None = None) -> str:
        return tool_text(self.call(name, arguments))


# --------------------------------------------------------------------------
# Prose parsers — every miss is None, never 0
# --------------------------------------------------------------------------

_DATE_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2})\b:?\s*(.*)$")
_RE_HRV_AVG = re.compile(r"HRV Avg:\s*(\d+)\s*ms(?:\s*[—–-]+\s*([A-Za-z][A-Za-z ]*?))?\s*$")
_RE_RANGE = re.compile(r"Normal Range:\s*(\d+)\s*-\s*(\d+)\s*ms")
_RE_BASELINE = re.compile(r"Baseline:\s*(\d+)\s*ms")
_RE_BPM = re.compile(r"^(\d+)\s*bpm\b")
_RE_STL = re.compile(r"^Short-Term Load:\s*(\d+)\s*$")
_RE_LTL = re.compile(r"^Long-Term Load:\s*(\d+)\s*$")
_RE_RATIO = re.compile(r"^Load Ratio:\s*(\d+(?:\.\d+)?)\s*$")
_RE_COMMENT = re.compile(r"^Comment:\s*(.+?)\s*$")


def _to_date(iso: str) -> date | None:
    try:
        return date.fromisoformat(iso)
    except ValueError:
        return None


def _day_blocks(text: str) -> dict[date, list[str]]:
    """Group lines under the date line that precedes them. A date seen twice
    keeps its FIRST block (assessments precede time series)."""
    blocks: dict[date, list[str]] = {}
    current: date | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _DATE_LINE.match(line) if not raw[:1].isspace() else None
        if m and _to_date(m.group(1)):
            d = _to_date(m.group(1))
            if d in blocks:
                current = None  # duplicate section — ignore its lines
                continue
            current = d
            blocks[d] = [m.group(2)] if m.group(2) else []
        elif current is not None:
            blocks[current].append(line)
    return blocks


def _first(regex, lines, group=1, cast=int):
    for line in lines:
        m = regex.search(line)
        if m:
            try:
                return cast(m.group(group))
            except (TypeError, ValueError):
                return None
    return None


def parse_sleep_hrv(text: str) -> dict[date, dict]:
    """querySleepHrv → {wake-up date: {hrv_ms, hrv_eval, hrv_normal_low,
    hrv_normal_high, hrv_baseline}}. The raw time-series section is ignored."""
    assessment = re.split(r"Sleep HRV Time Series", text, maxsplit=1)[0]
    out = {}
    for d, lines in _day_blocks(assessment).items():
        out[d] = {
            "hrv_ms": _first(_RE_HRV_AVG, lines),
            "hrv_eval": _first(_RE_HRV_AVG, lines, group=2, cast=str),
            "hrv_normal_low": _first(_RE_RANGE, lines, group=1),
            "hrv_normal_high": _first(_RE_RANGE, lines, group=2),
            "hrv_baseline": _first(_RE_BASELINE, lines),
        }
    return out


def parse_resting_hr(text: str) -> dict[date, int | None]:
    """queryRestingHeartRate → {date: bpm or None}. 'No data' is None."""
    return {d: _first(_RE_BPM, lines) for d, lines in _day_blocks(text).items()}


def parse_training_load(text: str) -> dict[date, dict]:
    """queryTrainingLoadAssessment → {date: {ati, cti, load_ratio, comment}}.
    Short-Term Load is COROS's ati, Long-Term Load its cti (30/30 days)."""
    out = {}
    for d, lines in _day_blocks(text).items():
        out[d] = {
            "ati": _first(_RE_STL, lines),
            "cti": _first(_RE_LTL, lines),
            "load_ratio": _first(_RE_RATIO, lines, cast=float),
            "comment": _first(_RE_COMMENT, lines, cast=str),
        }
    return out


# --------------------------------------------------------------------------
# Derivations
# --------------------------------------------------------------------------

def fatigue_state_for(tired_rate: float, cti: float) -> int | None:
    """COROS tiredRateStateNew: zone of tiredRateNew (= −tib) with bounds
    scaled by cti. 82/84 on the verified sample; two boundary days differ."""
    if cti is None or cti <= 0:
        return None
    for upper, state in ((-cti / 2, 1), (-0.1 * cti, 2), (0.05 * cti, 3), (0.8 * cti, 4)):
        if tired_rate < upper:
            return state
    return 5


def load_ratio_state_for(ratio: float) -> int | None:
    """COROS trainingLoadRatioState: fixed zones, 84/84 on the sample."""
    if ratio is None:
        return None
    for upper, state in ((0.5, 1), (0.8, 2), (1.0, 3), (1.5, 4)):
        if ratio < upper:
            return state
    return 5


def recommended_band(cti_prev_sunday: float) -> tuple[float, float] | None:
    """recomendTlMin/Max for the week = 7 × cti on the previous Sunday, ×1.8."""
    if cti_prev_sunday is None:
        return None
    lo = round(7 * cti_prev_sunday, 1)
    return lo, round(lo * 1.8, 1)


def derive_load_fields(ati, cti, load_ratio) -> dict:
    out = {"tib": None, "fatigue_pct": None, "fatigue_state": None, "load_ratio_state": None}
    if ati is not None and cti is not None:
        tib = float(cti) - float(ati)
        out["tib"] = tib
        out["fatigue_pct"] = -tib
        out["fatigue_state"] = fatigue_state_for(-tib, float(cti))
    if load_ratio is not None:
        out["load_ratio_state"] = load_ratio_state_for(float(load_ratio))
    return out


def gate_rows(sleep_hrv: dict, resting_hr: dict, load: dict) -> dict[date, dict]:
    """Merge the three parsers by date into recovery_snapshots-shaped rows."""
    rows = {}
    for d in sorted(set(sleep_hrv) | set(resting_hr) | set(load)):
        h = sleep_hrv.get(d) or {}
        l = load.get(d) or {}
        row = {
            "hrv_ms": h.get("hrv_ms"),
            "hrv_baseline": h.get("hrv_baseline"),
            "hrv_eval": h.get("hrv_eval"),
            "hrv_normal_low": h.get("hrv_normal_low"),
            "hrv_normal_high": h.get("hrv_normal_high"),
            "resting_hr": resting_hr.get(d),
            "ati": l.get("ati"),
            "cti": l.get("cti"),
            "load_ratio": l.get("load_ratio"),
            "load_comment": l.get("comment"),
        }
        row.update(derive_load_fields(row["ati"], row["cti"], row["load_ratio"]))
        rows[d] = row
    return rows


def missing_gate_fields(row: dict | None) -> list[str]:
    if not row:
        return list(GATE_FIELDS)
    return [f for f in GATE_FIELDS if row.get(f) is None]


# --------------------------------------------------------------------------
# Shadow run
# --------------------------------------------------------------------------

def shadow_enabled() -> bool:
    """On when a token exists and COROS_MCP_SHADOW isn't "0". Tests set it to
    "0" in conftest so no test ever reaches COROS."""
    flag = os.getenv("COROS_MCP_SHADOW", "1").strip().lower()
    if flag in ("0", "false", "off", "no"):
        return False
    return find_token_path() is not None


def fetch_gate_rows(days: int = 7) -> dict:
    """Network side: three read tools → parsed rows. Thread-safe (no DB).
    querySleepHrv caps recent-days at 7; the others accept more."""
    started = time.monotonic()
    client = McpClient(ensure_token())
    hrv = parse_sleep_hrv(client.call_text("querySleepHrv", {"days": min(days, 7)}))
    rhr = parse_resting_hr(client.call_text("queryRestingHeartRate", {"days": days}))
    load = parse_training_load(client.call_text("queryTrainingLoadAssessment", {"days": days}))
    rows = gate_rows(hrv, rhr, load)
    return {
        "rows": rows,
        "calls": client.calls,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def compare_with_snapshot(row: dict | None, snapshot) -> dict:
    """Per-field MCP vs DB for today. Ints exact, floats within TOLERANCE.
    `mcp_missing` = MCP None where DB has a value (parser or COROS gap);
    `db_missing` = the reverse (scraper gap)."""
    fields, mismatches, mcp_missing, db_missing = {}, [], [], []
    for f in COMPARE_FIELDS:
        m = (row or {}).get(f)
        d = getattr(snapshot, f, None) if snapshot is not None else None
        match = None
        if m is None and d is not None:
            mcp_missing.append(f)
            match = False
        elif d is None and m is not None:
            db_missing.append(f)
            match = False
        elif m is not None and d is not None:
            match = abs(float(m) - float(d)) <= TOLERANCE.get(f, 0.0)
            if not match:
                mismatches.append(f)
        fields[f] = {"mcp": m, "db": d, "match": match}
    return {"fields": fields, "mismatches": mismatches,
            "mcp_missing": mcp_missing, "db_missing": db_missing}


def shadow_report(today: date, snapshot, fetch: dict | None = None,
                  error: str | None = None) -> dict:
    """The `mcp_shadow` document on the refresh event. Never raises.
    status: ok | diff | no_today_row | no_snapshot | error. `no_snapshot` means
    the MCP side answered but the scrape left no row for today to compare
    against — a scraper verdict, never a pass for the MCP."""
    report = {"schema": 1, "date": today.isoformat(), "status": None,
              "elapsed_ms": None, "calls": None, "history_days": None,
              "missing": None, "snapshot_present": snapshot is not None}
    if error:
        report["status"] = "error"
        report["reason"] = error[:200]
        return report
    rows = (fetch or {}).get("rows") or {}
    report["elapsed_ms"] = (fetch or {}).get("elapsed_ms")
    report["calls"] = (fetch or {}).get("calls")
    report["history_days"] = len(rows)
    row = rows.get(today)
    if row is None:
        report["status"] = "no_today_row"
        report["missing"] = list(GATE_FIELDS)
        return report
    report["missing"] = missing_gate_fields(row)
    report["hrv_eval"] = row.get("hrv_eval")
    report["load_comment"] = row.get("load_comment")
    report.update(compare_with_snapshot(row, snapshot))
    if snapshot is None:
        report["status"] = "no_snapshot"
    elif report["mismatches"] or report["mcp_missing"]:
        report["status"] = "diff"
    else:
        report["status"] = "ok"
    return report
