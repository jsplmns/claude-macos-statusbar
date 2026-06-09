#!/usr/bin/env python3
"""
Claude Usage Tracker  ·  macOS menu bar
Run:     python3 claude_tracker.py
Install: bash install_claude_tracker.sh
"""

from __future__ import annotations   # allow `str | None` hints on Python 3.9

import http.client
import json
import ssl
import subprocess
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import rumps
except ImportError:
    print(
        "\n❌  'rumps' is not installed.\n"
        f"   Python: {sys.executable}\n"
        f"   Fix:    {sys.executable} -m pip install rumps\n",
        file=sys.stderr,
    )
    sys.exit(1)
except Exception as e:
    print(f"\n❌  Failed to import rumps: {e}\n", file=sys.stderr)
    traceback.print_exc()
    sys.exit(1)


# ── Config ─────────────────────────────────────────────────────────────────────

APP_NAME      = "Claude Tracker"   # shown in system dialogs (Touch ID, alerts)
CONFIG_PATH   = Path.home() / ".claude_tracker.json"
HISTORY_PATH  = Path.home() / ".claude_usage_history.jsonl"
ICON_PATH     = Path(__file__).resolve().parent / "assets" / "icon.png"

# The session key is a full-access claude.ai credential, so it lives in the
# macOS login Keychain rather than the plaintext config file.
KEYCHAIN_SERVICE = "claude-tracker"
KEYCHAIN_ACCOUNT = "sessionKey"
HISTORY_INTERVAL_MIN = 15   # minimum minutes between history writes
HISTORY_MAX_DAYS     = 90   # rolling retention window

# Bump when a stored field's meaning changes and old configs must self-heal.
# v2: org selection became capability-aware (Teams/Pro/Max vs. the empty
#     personal org), so any org_id picked by the old "first org" logic is
#     cleared on upgrade and re-detected.
SCHEMA_VERSION = 2

DEFAULTS: dict = {
    "messages_used":    0,
    "messages_limit":   45,
    "reset_time":       None,
    "last_updated":     None,
    "session_key":      "",
    "org_id":           "",
    "live_mode":        False,
    "utilization_5h":   None,
    "utilization_7d":   None,
    "utilization_opus_7d":   None,
    "utilization_sonnet_7d": None,
    "reset_7d":         None,
    "plan":             None,
    # Identity / account metadata for the Dashboard (cached once per connect).
    "account_name":     None,
    "account_email":    None,
    "org_name":         None,
    "org_role":         None,
    "seat_tier":        None,
    "billing_type":     None,
    "org_count":        None,
    "team_seats":       None,
    "schema_version":   SCHEMA_VERSION,
}


# ── Keychain (session key storage) ───────────────────────────────────────────

# Friendly label/description shown in Keychain Access and any system prompt.
KEYCHAIN_LABEL   = "Claude Usage Tracker"
KEYCHAIN_COMMENT = ("claude.ai session key, stored locally by Claude Usage Tracker. "
                    "It is sent only to claude.ai to read your usage — never anywhere else.")

# In-memory cache of the last value we wrote/read, so a steady stream of config
# saves never re-invokes `security` (and so never risks a repeat prompt).
_kc_cache: dict = {"key": None}


def _keychain_set(secret: str) -> bool:
    """Store/replace the session key in the login Keychain (no prompts later)."""
    if _kc_cache.get("key") == secret:
        return True
    try:
        # -U replaces an existing item; -T grants the security tool read access
        # so the background agent can fetch it without an interactive prompt.
        # -l / -j give the item a clear, reassuring name and description.
        subprocess.run(
            ["security", "add-generic-password", "-a", KEYCHAIN_ACCOUNT,
             "-s", KEYCHAIN_SERVICE, "-l", KEYCHAIN_LABEL, "-j", KEYCHAIN_COMMENT,
             "-w", secret, "-U", "-T", "/usr/bin/security"],
            check=True, capture_output=True,
        )
        _kc_cache["key"] = secret
        return True
    except Exception:
        return False


def _keychain_get() -> str:
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-a", KEYCHAIN_ACCOUNT,
             "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            key = r.stdout.strip()
            _kc_cache["key"] = key
            return key
    except Exception:
        pass
    return ""


def _keychain_delete() -> None:
    if _kc_cache.get("key") in ("", None):
        return
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-a", KEYCHAIN_ACCOUNT,
             "-s", KEYCHAIN_SERVICE],
            capture_output=True,
        )
    except Exception:
        pass
    _kc_cache["key"] = ""


def _touch_id_reason() -> str:
    """The Touch ID prompt reason, in the user's system language when known.

    macOS writes the surrounding dialog text in the user's language, so the
    reason should match — otherwise (e.g. on a German Mac) you get an English
    sentence inside a German prompt. Falls back to English for other locales.
    """
    translations = {
        "en": "Confirm it's you to save your Claude session key securely in your Keychain.",
        "de": "Bestätige, dass du es bist, um deinen Claude-Sitzungsschlüssel sicher im Schlüsselbund zu speichern.",
        "es": "Confirma que eres tú para guardar tu clave de sesión de Claude de forma segura en el Llavero.",
        "fr": "Confirmez que c'est bien vous pour enregistrer votre clé de session Claude en toute sécurité dans votre trousseau.",
        "it": "Conferma la tua identità per salvare la chiave di sessione di Claude in modo sicuro nel portachiavi.",
        "pt": "Confirme que é você para guardar a sua chave de sessão do Claude com segurança nas Senhas.",
        "nl": "Bevestig dat jij het bent om je Claude-sessiesleutel veilig in je sleutelhanger te bewaren.",
    }
    lang = "en"
    try:
        from Foundation import NSLocale
        prefs = NSLocale.preferredLanguages()
        if prefs and len(prefs) > 0:
            lang = str(prefs[0]).split("-")[0].split("_")[0].lower()
    except Exception:
        pass
    return translations.get(lang, translations["en"])


def touch_id_confirm(reason: str) -> bool:
    """Confirm the user's identity via Touch ID (with login-password fallback).

    Returns True when authenticated — and also when no biometric/password auth
    is configured or the framework is missing, so Macs without Touch ID are
    never blocked from connecting. Returns False only when the user is actually
    prompted and then cancels or fails.
    """
    try:
        import LocalAuthentication as LA
    except Exception:
        return True
    try:
        ctx    = LA.LAContext.alloc().init()
        policy = LA.LAPolicyDeviceOwnerAuthentication   # Touch ID OR login password
        # Leave the fallback button to macOS so it's shown in the user's
        # language (setting our own string would force English into an
        # otherwise localized dialog).
        can, _ = ctx.canEvaluatePolicy_error_(policy, None)
        if not can:
            return True

        import threading
        out  = {"ok": False}
        done = threading.Event()

        def reply(success, error):
            out["ok"] = bool(success)
            done.set()

        ctx.evaluatePolicy_localizedReason_reply_(policy, reason, reply)
        done.wait(timeout=60)
        return out["ok"]
    except Exception:
        return True


def load_cfg() -> dict:
    if not CONFIG_PATH.exists():
        cfg = DEFAULTS.copy()
        save_cfg(cfg)
        return cfg
    try:
        d = json.loads(CONFIG_PATH.read_text())
    except Exception:
        cfg = DEFAULTS.copy()
        save_cfg(cfg)
        return cfg

    cfg = DEFAULTS.copy()
    cfg.update(d)
    dirty = False

    # Self-heal older configs: re-detect the subscription org (v2 logic).
    if d.get("schema_version", 1) < SCHEMA_VERSION:
        cfg["org_id"]         = ""
        cfg["schema_version"] = SCHEMA_VERSION
        dirty = True

    # Session key lives in the Keychain. Migrate any plaintext key found in the
    # file, then always source the in-memory value from the Keychain.
    disk_key = d.get("session_key") or ""
    kc_key   = _keychain_get()
    if disk_key and not kc_key:
        _keychain_set(disk_key)
        kc_key = disk_key
    cfg["session_key"] = kc_key
    if disk_key:                 # scrub the plaintext copy from disk
        dirty = True

    if dirty:
        save_cfg(cfg)
    return cfg


def save_cfg(cfg: dict) -> None:
    try:
        to_disk = dict(cfg)
        key = to_disk.get("session_key") or ""
        if key:
            _keychain_set(key)
        else:
            _keychain_delete()
        to_disk["session_key"] = ""   # never persist the secret in plaintext
        CONFIG_PATH.write_text(json.dumps(to_disk, indent=2))
    except Exception:
        pass


# ── Usage history ──────────────────────────────────────────────────────────────

def _read_history() -> list:
    """Return history records within the retention window (oldest first)."""
    if not HISTORY_PATH.exists():
        return []
    cutoff = datetime.now() - timedelta(days=HISTORY_MAX_DAYS)
    records = []
    try:
        for line in HISTORY_PATH.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if datetime.fromisoformat(r["ts"]) >= cutoff:
                    records.append(r)
            except Exception:
                pass
    except Exception:
        pass
    return records


def append_history(cfg: dict) -> None:
    """Append a usage snapshot if >= HISTORY_INTERVAL_MIN have passed."""
    util_5h = cfg.get("utilization_5h")
    if util_5h is None:
        return  # only log when we have live data

    # Throttle: skip if last record is too recent
    records = _read_history()
    if records:
        try:
            last_ts = datetime.fromisoformat(records[-1]["ts"])
            if (datetime.now() - last_ts).total_seconds() < HISTORY_INTERVAL_MIN * 60:
                return
        except Exception:
            pass

    record = {
        "ts":             datetime.now().isoformat(timespec="minutes"),
        "utilization_5h": round(util_5h, 1),
        "utilization_7d": round(cfg.get("utilization_7d") or 0.0, 1),
        "messages_used":  cfg.get("messages_used", 0),
        "messages_limit": cfg.get("messages_limit", 45),
    }
    try:
        with HISTORY_PATH.open("a") as f:
            f.write(json.dumps(record) + "\n")
        _prune_history()
    except Exception:
        pass


def _prune_history() -> None:
    """Remove records older than HISTORY_MAX_DAYS from the history file."""
    if not HISTORY_PATH.exists():
        return
    cutoff = datetime.now() - timedelta(days=HISTORY_MAX_DAYS)
    try:
        kept = []
        for line in HISTORY_PATH.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                if datetime.fromisoformat(json.loads(line)["ts"]) >= cutoff:
                    kept.append(line)
            except Exception:
                pass
        HISTORY_PATH.write_text("\n".join(kept) + ("\n" if kept else ""))
    except Exception:
        pass


def daily_peaks(records: list) -> dict:
    """Map each date (YYYY-MM-DD) → peak 5h utilization recorded that day."""
    daily: dict = {}
    for r in records:
        try:
            date = r["ts"][:10]
            pct  = float(r.get("utilization_5h", 0))
            daily[date] = max(daily.get(date, 0.0), pct)
        except Exception:
            pass
    return daily


def _fmt_day(date: str, fmt: str = "%a %d %b") -> str:
    """'2026-06-09' → 'Mon 09 Jun' (falls back to the raw string)."""
    try:
        return datetime.strptime(date, "%Y-%m-%d").strftime(fmt)
    except Exception:
        return date


def format_history_summary() -> str:
    """Plain-text daily-peak summary — fallback when the drawn chart can't load."""
    daily = daily_peaks(_read_history())
    if not daily:
        return (
            "No usage history recorded yet.\n\n"
            "History is logged every 15 minutes while connected."
        )

    sorted_days = sorted(daily.keys(), reverse=True)[:30]
    bar_w = 16
    lines = [f"{'Day':<12}{'Peak (5h window)':<22}", "─" * 40]
    for day in sorted_days:
        pct    = daily[day]
        filled = round(bar_w * pct / 100)
        bar    = "█" * filled + "·" * (bar_w - filled)
        lines.append(f"{_fmt_day(day):<11} {bar}  {pct:>3.0f}%")

    return "\n".join(lines)


# ── Formatting helpers ─────────────────────────────────────────────────────────

def countdown(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        dt  = datetime.fromisoformat(iso)
        now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now()
        diff = dt - now
        if diff.total_seconds() <= 0:
            return "now"
        s = int(diff.total_seconds())
        d, r = divmod(s, 86400)
        h, r = divmod(r, 3600)
        m    = r // 60
        if d:  return f"{d}d {h}h"          # e.g. 7-day window
        if h:  return f"{h}h {m:02d}m"      # e.g. 5-hour window
        return f"{m}m"
    except Exception:
        return None


def time_ago(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        dt  = datetime.fromisoformat(iso)
        now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now()
        s   = int((now - dt).total_seconds())
        if s < 10:
            return "just now"
        if s < 60:
            return f"{s}s ago"
        return f"{s // 60}m ago"
    except Exception:
        return None


def _pct_and_cd(cfg: dict) -> tuple[float, str | None]:
    live_pct = cfg.get("utilization_5h")
    pct = (
        live_pct
        if live_pct is not None
        else ((cfg["messages_used"] / cfg["messages_limit"] * 100)
              if cfg["messages_limit"] > 0 else 0.0)
    )
    return pct, countdown(cfg.get("reset_time"))


# ── Claude.ai API helpers ──────────────────────────────────────────────────────

_HEADERS = {
    "Accept":          "application/json",
    "Content-Type":    "application/json",
    # Safari UA — coherent with the macOS (NSURLSession) TLS fingerprint we
    # send requests through, so Cloudflare sees a consistent, native-looking
    # client instead of a UA/TLS mismatch that screams "bot".
    "User-Agent":      (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15"
    ),
    "Referer":         "https://claude.ai/",
    "Origin":          "https://claude.ai",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Site":  "same-origin",
    "Sec-Fetch-Mode":  "cors",
    "Sec-Fetch-Dest":  "empty",
}


def _looks_like_cloudflare(raw: str | None) -> bool:
    r = (raw or "").lower()
    return any(s in r for s in (
        "just a moment", "cf-browser-verification", "cf-chl", "/cdn-cgi/",
        "attention required", "enable javascript and cookies",
    ))


def _ssl_context() -> ssl.SSLContext:
    """A verified SSL context that works even on a fresh python.org install.

    python.org's Python ships without populating the system trust store, so a
    plain create_default_context() raises 'unable to get local issuer
    certificate' and every request fails. certifi (installed into the venv)
    provides a known-good CA bundle and sidesteps that entirely.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _get_nsurl(session_key: str, path: str) -> tuple[int, dict | None, str]:
    """Fetch via macOS's own networking stack (NSURLSession).

    This is the same stack Safari uses, so the TLS/HTTP fingerprint looks like
    native Mac traffic — which Cloudflare lets through far more reliably than
    Python's, the usual reason connecting works on one Mac but not another.
    """
    import threading
    from Foundation import (NSURL, NSMutableURLRequest, NSURLSession,
                            NSURLSessionConfiguration, NSString,
                            NSUTF8StringEncoding)

    url = NSURL.URLWithString_("https://claude.ai" + path)
    req = NSMutableURLRequest.requestWithURL_cachePolicy_timeoutInterval_(url, 1, 15.0)
    req.setHTTPMethod_("GET")
    req.setValue_forHTTPHeaderField_(f"sessionKey={session_key}", "Cookie")
    for k, v in _HEADERS.items():
        req.setValue_forHTTPHeaderField_(v, k)

    session = NSURLSession.sessionWithConfiguration_(
        NSURLSessionConfiguration.ephemeralSessionConfiguration())
    out  = {"status": 0, "raw": ""}
    done = threading.Event()

    def handler(data, response, error):
        try:
            if error is not None:
                out["raw"] = str(error.localizedDescription())
            else:
                if response is not None:
                    out["status"] = int(response.statusCode())
                if data is not None:
                    s = NSString.alloc().initWithData_encoding_(data, NSUTF8StringEncoding)
                    out["raw"] = str(s) if s is not None else ""
        finally:
            done.set()

    task = session.dataTaskWithRequest_completionHandler_(req, handler)
    task.resume()
    if not done.wait(timeout=20):
        return 0, None, "timed out"
    raw = out["raw"]
    try:
        body = json.loads(raw)
    except Exception:
        body = None
    return out["status"], body, raw


def _get_httpclient(session_key: str, path: str) -> tuple[int, dict | None, http.client.HTTPResponse, str]:
    try:
        c = http.client.HTTPSConnection("claude.ai", timeout=10, context=_ssl_context())
        c.request("GET", path, headers={**_HEADERS, "Cookie": f"sessionKey={session_key}"})
        r   = c.getresponse()
        raw = r.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = None
        return r.status, body, r, raw
    except Exception as e:
        return 0, None, None, str(e)


def _get(session_key: str, path: str) -> tuple[int, dict | None, http.client.HTTPResponse | None, str]:
    # Prefer the OS networking stack (native fingerprint passes Cloudflare); fall
    # back to Python's http.client only if it isn't usable (e.g. no PyObjC).
    try:
        status, body, raw = _get_nsurl(session_key, path)
        if status != 0:
            return status, body, None, raw
    except Exception:
        pass
    return _get_httpclient(session_key, path)


def _select_org(orgs: list) -> dict | None:
    """Pick the org that holds the user's claude.ai subscription usage.

    /api/organizations can return several orgs. Picking the first one is wrong
    for Teams/Enterprise (and often Pro/Max) accounts, where it's typically the
    auto-created personal org — capabilities ['chat'], no billing — which always
    reports 0% usage. The subscription lives on a chat-capable org with active
    billing; Teams/Enterprise additionally carry the 'raven' capability. API /
    Console orgs (capabilities ['api'], billing 'api_evaluation') aren't
    claude.ai usage at all. Score every org and take the best match.
    """
    def score(o: dict) -> tuple:
        caps     = o.get("capabilities") or []
        chat     = "chat" in caps
        team     = "raven" in caps                      # Teams / Enterprise
        billing  = o.get("billing_type")
        billed   = bool(billing) and billing != "api_evaluation"
        return (chat, billed, team)

    candidates = [o for o in orgs if isinstance(o, dict)]
    return max(candidates, key=score) if candidates else None


def fetch_org_id(session_key: str) -> str | None:
    status, body, _, _ = _get(session_key, "/api/organizations")
    if status == 200 and isinstance(body, list) and body:
        org = _select_org(body)
        if org:
            return org.get("uuid") or org.get("id")
    return None


def fetch_live_usage(session_key: str, org_id: str) -> dict | None:
    attempts = [
        f"/api/organizations/{org_id}/usage",
        f"/api/organizations/{org_id}/limits",
        f"/api/organizations/{org_id}/rate_limits",
        f"/api/organizations/{org_id}",
        "/api/account",
    ]
    for path in attempts:
        status, body, resp, _ = _get(session_key, path)
        if status not in (200, 206) or not body:
            continue
        result = _parse_usage_body(body)
        if result:
            return result
        if resp:
            result = _parse_usage_headers(resp)
            if result:
                return result
    return None


def _plan_label(org: dict) -> str:
    """Human-readable plan name derived from an organization object.

    claude.ai doesn't expose a tidy 'plan' field, but the org's rate-limit tier
    and raven_type encode it: Team/Enterprise via raven, otherwise the consumer
    tiers (Free / Pro / Max, with Max carrying a 5× or 20× multiplier).
    """
    raven = (org.get("raven_type") or "").lower()
    tier  = (org.get("rate_limit_tier") or "").lower()
    if raven == "team" or "raven" in tier:
        return "Team"
    if raven in ("enterprise", "ent"):
        return "Enterprise"
    if "max" in tier:
        if "20" in tier: return "Max 20×"
        if "5"  in tier: return "Max 5×"
        return "Max"
    if "pro" in tier:
        return "Pro"
    if "free" in tier or tier in ("default_claude_ai", ""):
        return "Free"
    return tier.replace("default_", "").replace("_", " ").title()


def pro_threshold(plan: str | None) -> float:
    """Utilization % that roughly equals a Pro plan's capacity, given the tier.

    Max 20× grants ~20× Pro, so a full Pro plan ≈ 5% of a Max-20× window;
    Max 5× ≈ 20%. For other tiers we fall back to the common ~1/5 heuristic.
    Peaks under this line would also have fit on Pro — above it is where a
    larger plan actually earns its cost.
    """
    p = (plan or "").lower()
    if "20" in p:
        return 5.0
    return 20.0


def _pretty_role(role: str | None) -> str | None:
    if not role:
        return None
    return role.replace("_", " ").title()   # primary_owner → Primary Owner


def _fetch_team_seats(session_key: str, org_id: str) -> str | None:
    """Seat composition for a Team org, e.g. '5 members · 4 standard · 1 premium'.

    Uses the members list (admin/owner only) — returns None when not permitted.
    """
    status, members, _, _ = _get(
        session_key, f"/api/organizations/{org_id}/members?limit=200")
    if status != 200 or not isinstance(members, list) or not members:
        return None
    std  = sum(1 for m in members if (m.get("seat_tier") or "") == "team_standard")
    prem = len(members) - std
    parts = []
    if std:  parts.append(f"{std} standard")
    if prem: parts.append(f"{prem} premium")
    suffix = "  ·  " + "  ·  ".join(parts) if parts else ""
    return f"{len(members)} members{suffix}"


def fetch_identity(session_key: str, org_id: str) -> dict:
    """Account + plan metadata for the Dashboard, tailored to the plan type.

    Works for every plan and role:
      • Personal (Pro / Max / Free) → account + plan only (no org/role/seats —
        the auto-created personal org isn't meaningful to show).
      • Team / Enterprise → also org name + your role; seat composition too,
        but only for admins (the members list is owner/admin-only; a regular
        member simply won't get that line, with no error).

    One org call + one bootstrap call, plus one members call for team admins.
    """
    out: dict = {}
    is_org_plan = False
    org_name    = None

    status, org, _, _ = _get(session_key, f"/api/organizations/{org_id}")
    if status == 200 and isinstance(org, dict):
        out["plan"]         = _plan_label(org)
        out["billing_type"] = org.get("billing_type")
        caps                = org.get("capabilities") or []
        is_org_plan         = ("raven" in caps) or bool(org.get("raven_type"))
        org_name            = org.get("name")

    role = seat = None
    status, boot, _, _ = _get(session_key, "/api/bootstrap")
    if status == 200 and isinstance(boot, dict):
        acct = boot.get("account") or {}
        out["account_name"]  = acct.get("full_name") or acct.get("display_name")
        out["account_email"] = acct.get("email_address")
        mems = acct.get("memberships") or []
        out["org_count"] = len(mems) or None
        for m in mems:
            if (m.get("organization") or {}).get("uuid") == org_id:
                role = m.get("role")
                seat = m.get("seat_tier")
                break

    # Org context only makes sense for Team/Enterprise plans. Personal plans
    # leave org_name / org_role / seat_tier / team_seats unset → those lines
    # simply don't appear in the Dashboard.
    if is_org_plan:
        out["org_name"]  = org_name
        out["org_role"]  = _pretty_role(role)
        out["seat_tier"] = seat
        # Team Standard vs Premium from the user's own seat tier. Standard
        # seats report "team_standard"; premium report "team_tier_1" (or similar).
        if out.get("plan") == "Team":
            s = (seat or "").lower()
            if s and s != "team_standard":
                out["plan"] = "Team Premium"
        out["team_seats"] = _fetch_team_seats(session_key, org_id)  # None unless admin

    return out


def _parse_usage_body(body: dict | list) -> dict | None:
    if isinstance(body, list):
        return None

    if "five_hour" in body or "seven_day" in body:
        result: dict = {}
        fh = body.get("five_hour")
        sd = body.get("seven_day")
        if isinstance(fh, dict) and fh.get("utilization") is not None:
            result["utilization_5h"] = float(fh["utilization"])
            result["reset_time"]     = fh.get("resets_at")
        if isinstance(sd, dict) and sd.get("utilization") is not None:
            result["utilization_7d"] = float(sd["utilization"])
            result["reset_7d"]       = sd.get("resets_at")
        # Per-model weekly limits (Opus is usually the first to run out on Max).
        opus = body.get("seven_day_opus")
        son  = body.get("seven_day_sonnet")
        if isinstance(opus, dict) and opus.get("utilization") is not None:
            result["utilization_opus_7d"] = float(opus["utilization"])
        if isinstance(son, dict) and son.get("utilization") is not None:
            result["utilization_sonnet_7d"] = float(son["utilization"])
        if result:
            return result

    u = body.get("usage", {})
    if isinstance(u, dict):
        msgs  = u.get("messages", u)
        used  = msgs.get("used") or msgs.get("messages_used")
        limit = msgs.get("limit") or msgs.get("messages_limit")
        reset = msgs.get("reset_at") or msgs.get("reset_time") or u.get("reset_at")
        if used is not None and limit is not None:
            return {"messages_used": int(used), "messages_limit": int(limit),
                    "reset_time": reset}

    for used_key in ("messages_used", "used_messages", "message_count"):
        for limit_key in ("messages_limit", "message_limit", "max_messages"):
            if used_key in body and limit_key in body:
                return {
                    "messages_used":  int(body[used_key]),
                    "messages_limit": int(body[limit_key]),
                    "reset_time":     body.get("reset_at") or body.get("reset_time"),
                }

    rl = body.get("rate_limit") or body.get("rateLimit", {})
    if isinstance(rl, dict):
        remaining = rl.get("remaining") or rl.get("requests_remaining")
        limit     = rl.get("limit") or rl.get("requests_limit")
        reset     = rl.get("reset") or rl.get("reset_at")
        if remaining is not None and limit is not None:
            return {"messages_used":  int(limit) - int(remaining),
                    "messages_limit": int(limit),
                    "reset_time":     reset}
    return None


def _parse_usage_headers(resp) -> dict | None:
    for prefix in ("anthropic-ratelimit-requests", "x-ratelimit-requests",
                   "ratelimit-requests"):
        remaining = resp.getheader(f"{prefix}-remaining")
        limit     = resp.getheader(f"{prefix}-limit")
        reset     = resp.getheader(f"{prefix}-reset")
        if remaining is not None and limit is not None:
            try:
                return {
                    "messages_used":  int(limit) - int(remaining),
                    "messages_limit": int(limit),
                    "reset_time":     reset,
                }
            except ValueError:
                pass
    return None


def validate_session_key(session_key: str) -> tuple[bool, str, str]:
    status, body, _, raw = _get(session_key, "/api/organizations")

    if status == 200 and isinstance(body, list):
        if body:
            org    = _select_org(body) or body[0]
            org_id = org.get("uuid") or org.get("id") or ""
            name   = org.get("name", "your account")
            return True, org_id, f"Connected to {name}"
        return False, "", "Connected, but no Claude workspace was found for this account."

    if status == 0:
        return False, "", ("Couldn't reach claude.ai. Check your internet connection, "
                           "then click Connect and try again.")

    if _looks_like_cloudflare(raw):
        return False, "", ("claude.ai is temporarily blocking automated requests from "
                           "this Mac (Cloudflare). Open claude.ai in your browser, wait "
                           "a moment, then try Connect again.")

    if status in (401, 403):
        return False, "", ("That session key wasn't accepted. In your browser at "
                           "claude.ai, copy the full value of the sessionKey cookie "
                           "(it starts with sk-ant-sid…) and paste it again.")

    return False, "", (f"Unexpected response from claude.ai (HTTP {status}). "
                       "Please try again in a moment.")


# ── App ────────────────────────────────────────────────────────────────────────

class ClaudeTracker(rumps.App):

    def __init__(self) -> None:
        super().__init__("", quit_button=None)
        self.cfg          = load_cfg()
        self._fetch_error = ""

        # ── Status display (non-interactive, grayed-out in menu) ──────────
        self._i_head   = rumps.MenuItem("Claude Usage")          # section header
        self._i_5h     = rumps.MenuItem("")                       # [bar]  5h   68%
        self._i_7d     = rumps.MenuItem("")                       # [bar]  7d   12%
        self._i_opus   = rumps.MenuItem("")                       # [bar]  Opus 7d   8%
        self._i_sonnet = rumps.MenuItem("")                       # [bar]  Sonnet 7d 2%
        self._i_timer  = rumps.MenuItem("")                       # ↺  Resets in 1h 42m
        self._i_source = rumps.MenuItem("")                       # ●  Live  —  2m ago

        # ── Last-3-days history (drawn mini-bars, populated in _refresh) ───
        self._i_hist_head = rumps.MenuItem("Last 3 days")
        self._i_hist      = [rumps.MenuItem(""), rumps.MenuItem(""), rumps.MenuItem("")]

        # ── Actions ───────────────────────────────────────────────────────
        self._conn    = rumps.MenuItem("Connect to Claude.ai…", callback=self.connect_claude)
        self._disconn = rumps.MenuItem("Disconnect",            callback=self.disconnect_claude)
        self._web     = rumps.MenuItem("Open Claude.ai  ↗",     callback=self.open_web)
        self._hist    = rumps.MenuItem("Dashboard",              callback=self.show_history)
        self._qui     = rumps.MenuItem("Quit",                   callback=rumps.quit_application)

        self.menu = [
            self._i_head,
            None,
            self._i_5h,
            self._i_7d,
            self._i_opus,
            self._i_sonnet,
            self._i_timer,
            None,
            self._i_hist_head,
            *self._i_hist,
            None,
            self._i_source,
            None,
            self._conn,
            self._disconn,
            None,
            self._web,
            self._hist,
            None,
            self._qui,
        ]

        self._refresh()

        if self.cfg.get("session_key") and self.cfg.get("live_mode"):
            self._do_live_fetch()

    # ── Status bar title ───────────────────────────────────────────────────

    @staticmethod
    def _fill_color(pct: float):
        """Green / amber / red NSColor for a utilisation percentage."""
        from AppKit import NSColor
        if   pct >= 90: return NSColor.systemRedColor()
        elif pct >= 65: return NSColor.systemOrangeColor()
        return NSColor.systemGreenColor()

    @classmethod
    def _draw_progress_image(cls, pct: float, width: float, height: float,
                             bar_h: float | None = None):
        """Render a slick, continuous, rounded progress pill as an NSImage.

        One smooth rounded fill over a faint rounded track — no segments. The
        fill is colour-coded by utilisation (green / amber / red) so the bar
        doubles as an at-a-glance warning. Not a template image, so the colour
        survives; the gray track uses an alpha that reads on light and dark.
        Shared by the menu-bar icon and the dropdown rows.
        """
        from AppKit import NSBezierPath, NSColor, NSImage

        p      = min(max(pct, 0.0), 100.0)
        W, H   = float(width), float(height)
        bh     = float(bar_h) if bar_h else H * 0.55
        radius = bh / 2.0
        y      = (H - bh) / 2.0

        img = NSImage.alloc().initWithSize_((W, H))
        img.lockFocus()

        # Track — faint rounded capsule spanning the full width
        NSColor.colorWithWhite_alpha_(0.5, 0.30).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ((0, y), (W, bh)), radius, radius
        ).fill()

        # Fill — colour-coded capsule. Clamp width to bh so a small non-zero
        # value still shows a rounded dot rather than nothing.
        if p > 0:
            fw = max(W * p / 100.0, bh)
            cls._fill_color(p).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                ((0, y), (fw, bh)), radius, radius
            ).fill()

        img.unlockFocus()
        img.setTemplate_(False)   # keep colour (template would force monochrome)
        return img

    @classmethod
    def _draw_bar_image(cls, pct: float):
        """Compact pill for the menu-bar icon."""
        return cls._draw_progress_image(pct, 42.0, 13.0, bar_h=7.0)

    @classmethod
    def _draw_menu_bar_row(cls, pct: float):
        """Wider pill for a dropdown-menu row."""
        return cls._draw_progress_image(pct, 150.0, 12.0, bar_h=7.0)

    def _set_title(self, pct: float, cd: str | None) -> None:
        """
        Status bar: a drawn pill-progress-bar (NSImage) to the left, percentage
        and reset countdown as plain text to the right.

        rumps owns the title text (set via ``self.title``); we attach the image
        directly onto the underlying NSStatusItem button. If PyObjC drawing is
        unavailable, the text title still renders on its own — no block bars.
        """
        suffix = ""
        if cd == "now": suffix = "  ↺"
        elif cd:        suffix = f"  {cd}"
        text = f"{pct:.0f}%{suffix}"

        # rumps draws this as the button title (gracefully no-ops pre-launch).
        # Two leading spaces = gap between the drawn bar and the text.
        self.title = f"  {text}"

        # Attach the slick drawn bar to the status item button, if we can reach
        # it. self._nsapp exists only once the app is running; before that the
        # text title above is enough.
        nsapp = getattr(self, "_nsapp", None)
        item  = getattr(nsapp, "nsstatusitem", None) if nsapp is not None else None
        if item is None:
            return
        try:
            btn = item.button()
            btn.setImage_(self._draw_bar_image(pct))
            btn.setImagePosition_(2)   # NSImageLeft
        except Exception:
            pass

    # ── Menu-row helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _set_row(item: "rumps.MenuItem", text: str, pct: float | None = None,
                 bar_width: float = 150.0) -> None:
        """Set a menu row's title and (optionally) a drawn progress bar to its left."""
        item.title = f"  {text}"
        try:
            item._menuitem.setHidden_(False)
            if pct is not None:
                img = ClaudeTracker._draw_progress_image(pct, bar_width, 12.0, bar_h=7.0)
                item._menuitem.setImage_(img)
            else:
                item._menuitem.setImage_(None)
        except Exception:
            pass

    @staticmethod
    def _hide_row(item: "rumps.MenuItem") -> None:
        try:
            item._menuitem.setHidden_(True)
        except Exception:
            item.title = ""

    def _refresh_history_rows(self) -> None:
        """Populate the three 'Last 3 days' rows with drawn mini-bars."""
        daily = daily_peaks(_read_history())
        days  = sorted(daily.keys(), reverse=True)[:3]

        if not days:
            self._set_row(self._i_hist[0], "No history yet")
            for it in self._i_hist[1:]:
                self._hide_row(it)
            return

        for i, item in enumerate(self._i_hist):
            if i < len(days):
                day = days[i]
                pct = daily[day]
                self._set_row(item, f"{_fmt_day(day)}    {pct:.0f}%", pct, bar_width=110.0)
            else:
                self._hide_row(item)

    # ── Display refresh ────────────────────────────────────────────────────

    def _refresh(self) -> None:
        cfg      = self.cfg
        used     = cfg["messages_used"]
        limit    = cfg["messages_limit"]
        pct      = (used / limit * 100) if limit > 0 else 0.0
        cd       = countdown(cfg.get("reset_time"))
        live_pct = cfg.get("utilization_5h")
        bar_pct  = live_pct if live_pct is not None else pct

        # ── Status bar: drawn pill + percentage ───────────────────────────
        self._set_title(bar_pct, cd)

        # ── Header: brand + detected plan ──────────────────────────────────
        plan = cfg.get("plan")
        self._i_head.title = f"Claude Usage  ·  {plan}" if plan else "Claude Usage"

        # ── Dropdown: drawn 5h / 7d bars (+ per-model weekly) ──────────────
        if live_pct is not None:
            self._set_row(self._i_5h, f"5h    {bar_pct:.0f}%", bar_pct)
            pct_7d = cfg.get("utilization_7d")
            if pct_7d is not None:
                self._set_row(self._i_7d, f"7d    {pct_7d:.0f}%", pct_7d)
            else:
                self._hide_row(self._i_7d)

            opus = cfg.get("utilization_opus_7d")
            son  = cfg.get("utilization_sonnet_7d")
            if opus is not None:
                self._set_row(self._i_opus, f"Opus 7d    {opus:.0f}%", opus)
            else:
                self._hide_row(self._i_opus)
            if son is not None:
                self._set_row(self._i_sonnet, f"Sonnet 7d    {son:.0f}%", son)
            else:
                self._hide_row(self._i_sonnet)
        else:
            self._set_row(self._i_5h, f"{used} / {limit} messages", bar_pct)
            self._hide_row(self._i_7d)
            self._hide_row(self._i_opus)
            self._hide_row(self._i_sonnet)

        # ── Last 3 days ────────────────────────────────────────────────────
        self._refresh_history_rows()

        # ── Reset countdown ────────────────────────────────────────────────
        if cd == "now":
            self._i_timer.title = "  ↺  Resetting…"
        elif cd:
            self._i_timer.title = f"  ↺  Resets in {cd}"
        else:
            self._i_timer.title = "  ↺  —"

        # ── Connection status ──────────────────────────────────────────────
        connected = bool(cfg.get("live_mode") and cfg.get("session_key"))
        if connected:
            synced = time_ago(cfg.get("last_updated"))
            if self._fetch_error:
                self._i_source.title = f"  ●  Error  —  {self._fetch_error}"
            elif synced:
                self._i_source.title = f"  ●  Live  —  {synced}"
            else:
                self._i_source.title = "  ●  Live"
            self._conn.title = "Reconnect…"
            self._disconn.set_callback(self.disconnect_claude)
        else:
            self._i_source.title = "  ○  Not connected"
            self._conn.title = "Connect to Claude.ai…"
            self._disconn.set_callback(None)   # grayed out when disconnected

    # ── Live fetch ─────────────────────────────────────────────────────────

    def _do_live_fetch(self) -> None:
        key    = self.cfg.get("session_key", "")
        org_id = self.cfg.get("org_id", "")
        if not key:
            return

        if not org_id:
            org_id = fetch_org_id(key) or ""
            if org_id:
                self.cfg["org_id"] = org_id
                save_cfg(self.cfg)

        # Account/org/plan metadata rarely changes — fetch once and cache.
        if org_id and not (self.cfg.get("plan") and self.cfg.get("account_email")):
            ident = fetch_identity(key, org_id)
            if ident:
                self.cfg.update({k: v for k, v in ident.items() if v is not None})
                save_cfg(self.cfg)

        data = fetch_live_usage(key, org_id) if org_id else None

        if data:
            self._fetch_error = ""
            if "utilization_5h" in data:
                pct_5h = data["utilization_5h"]
                self.cfg["messages_used"] = round(
                    pct_5h / 100.0 * self.cfg["messages_limit"]
                )
                if data.get("reset_time"):
                    self.cfg["reset_time"] = data["reset_time"]
                self.cfg["utilization_5h"] = pct_5h
                self.cfg["utilization_7d"] = data.get("utilization_7d")
                self.cfg["reset_7d"]       = data.get("reset_7d")
                self.cfg["utilization_opus_7d"]   = data.get("utilization_opus_7d")
                self.cfg["utilization_sonnet_7d"] = data.get("utilization_sonnet_7d")
            else:
                for field in ("messages_used", "messages_limit", "reset_time"):
                    if field in data and data[field] is not None:
                        self.cfg[field] = data[field]
            self.cfg["last_updated"] = datetime.now().isoformat()
            save_cfg(self.cfg)
            append_history(self.cfg)
        else:
            self._fetch_error = "usage endpoint unavailable"

        self._refresh()

    # ── Connect / disconnect ───────────────────────────────────────────────

    _CONNECT_MSG = (
        "Find your session key in your browser:\n\n"
        "1. Open claude.ai  →  Cmd+Option+I\n"
        "2. Application tab  →  Cookies  →  claude.ai\n"
        "3. Copy the value of  sessionKey  (starts with  sk-ant-sid01…)\n\n"
        "It's saved in your macOS Keychain (encrypted) and sent only to "
        "claude.ai. You'll confirm with Touch ID."
    )

    def _prompt_for_key(self) -> str | None:
        """Branded key-entry dialog (icon + accessory text field).

        Returns the entered key, or None if cancelled. Falls back to the plain
        rumps.Window if the custom NSAlert can't be built.
        """
        try:
            from AppKit import NSAlert, NSTextField, NSImage
            from Foundation import NSMakeRect

            alert = NSAlert.alloc().init()
            alert.setMessageText_("Connect to Claude.ai")
            alert.setInformativeText_(self._CONNECT_MSG)
            alert.setAlertStyle_(0)   # informational
            if ICON_PATH.exists():
                icon = NSImage.alloc().initWithContentsOfFile_(str(ICON_PATH))
                if icon:
                    alert.setIcon_(icon)

            field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 360, 24))
            field.setPlaceholderString_("sk-ant-sid01…")
            field.setBezeled_(True)
            field.setEditable_(True)
            field.setSelectable_(True)
            alert.setAccessoryView_(field)

            alert.addButtonWithTitle_("Connect")
            alert.addButtonWithTitle_("Cancel")
            alert.window().setInitialFirstResponder_(field)

            if alert.runModal() == 1000:        # NSAlertFirstButtonReturn
                return (field.stringValue() or "").strip()
            return None
        except Exception:
            win = rumps.Window(
                message=self._CONNECT_MSG, title="Connect to Claude.ai",
                default_text="", ok="Connect", cancel="Cancel", dimensions=(440, 24),
            )
            resp = win.run()
            return (resp.text or "").strip() if resp.clicked else None

    def connect_claude(self, _) -> None:
        _icon = str(ICON_PATH) if ICON_PATH.exists() else None
        try:
            key = self._prompt_for_key()
            if not key:
                return

            # A pasted cookie sometimes arrives as `sessionKey=sk-ant-…` or with
            # surrounding quotes/whitespace — normalize before validating.
            key = key.strip().strip('"\'').strip()
            if key.lower().startswith("sessionkey="):
                key = key.split("=", 1)[1].strip()

            ok, org_id, message = validate_session_key(key)
            if not ok:
                rumps.alert(title="Couldn't connect", message=message,
                            ok="OK", icon_path=_icon)
                return

            # Confirm it's really you before writing the credential to the Keychain.
            if not touch_id_confirm(_touch_id_reason()):
                rumps.alert(title="Not saved",
                            message="Touch ID wasn't confirmed, so your session key "
                                    "was not saved. You can try connecting again anytime.",
                            ok="OK", icon_path=_icon)
                return

            cfg = load_cfg()
            cfg["session_key"] = key
            cfg["org_id"]      = org_id
            cfg["live_mode"]   = True
            for f in self._IDENTITY_FIELDS:   # re-detect for the freshly connected org
                cfg[f] = None
            save_cfg(cfg)
            self.cfg = cfg
            self._fetch_error = ""
            self._do_live_fetch()
            rumps.alert(title="Connected",
                        message=f"{message}\n\nYour session key is stored in the macOS "
                                "Keychain (encrypted) — never in a plain file — and is "
                                "only ever sent to claude.ai.\n\nUsage syncs every minute.",
                        ok="OK", icon_path=_icon)
        except Exception as e:
            # Never fail silently — surface the detail so it can be reported.
            try:
                (Path.home() / ".claude_tracker_crash.log").write_text(
                    f"[{datetime.now()}] connect error: {e}\n{traceback.format_exc()}\n")
            except Exception:
                pass
            rumps.alert(title="Couldn't connect",
                        message=f"Something went wrong while connecting:\n\n{e}\n\n"
                                "Please try again, or report this message.",
                        ok="OK", icon_path=_icon)

    # Identity fields cleared on connect (re-detect) and disconnect (privacy).
    _IDENTITY_FIELDS = ("plan", "account_name", "account_email", "org_name",
                        "org_role", "seat_tier", "billing_type", "org_count",
                        "team_seats")

    def disconnect_claude(self, _) -> None:
        cfg = load_cfg()
        cfg["session_key"] = ""
        cfg["org_id"]      = ""
        cfg["live_mode"]   = False
        for f in self._IDENTITY_FIELDS:
            cfg[f] = None
        save_cfg(cfg)
        self.cfg = cfg
        self._fetch_error = ""
        self._refresh()

    def open_web(self, _) -> None:
        subprocess.run(["open", "https://claude.ai"])

    # ── History chart ───────────────────────────────────────────────────────

    @classmethod
    def _draw_history_chart(cls, daily: dict, pro_pct: float = 20.0,
                            per_day: float = 18.0, min_width: float = 448.0,
                            height: float = 172.0):
        """Render the full daily-peak history (oldest→newest) as an NSImage.

        The width grows with the number of days (≈ per_day px each) so the
        Dashboard can scroll horizontally back through all available history.
        Falls back to a friendly empty state when there's no data yet.
        """
        from AppKit import (NSBezierPath, NSColor, NSImage, NSFont,
                            NSFontAttributeName, NSForegroundColorAttributeName)
        from Foundation import NSAttributedString

        keys = sorted(daily.keys())
        n    = len(keys)
        pad_l, pad_r, pad_t, pad_b = 16.0, 40.0, 14.0, 26.0
        W      = max(min_width, pad_l + pad_r + n * per_day)
        H      = height
        plot_w = W - pad_l - pad_r
        plot_h = H - pad_t - pad_b
        slot   = plot_w / n if n else plot_w

        def label(s, x, y, size=9.0, color=None):
            attrs = {
                NSFontAttributeName: NSFont.systemFontOfSize_(size),
                NSForegroundColorAttributeName: color or NSColor.secondaryLabelColor(),
            }
            NSAttributedString.alloc().initWithString_attributes_(s, attrs).drawAtPoint_((x, y))

        img = NSImage.alloc().initWithSize_((W, H))
        img.lockFocus()

        # Horizontal gridlines + axis labels at 0 / 50 / 100 %
        for frac in (0.0, 0.5, 1.0):
            gy = pad_b + plot_h * frac
            NSColor.colorWithWhite_alpha_(0.5, 0.18).setStroke()
            ln = NSBezierPath.bezierPath()
            ln.moveToPoint_((pad_l, gy)); ln.lineToPoint_((W - pad_r, gy))
            ln.setLineWidth_(0.5); ln.stroke()
            label(f"{int(frac * 100)}%", W - pad_r + 6, gy - 6)

        if n == 0:
            # Empty state — no history recorded yet.
            label("No usage history yet.", pad_l + 10, pad_b + plot_h / 2 + 4, 12.0)
            label("Your daily peaks will appear here as you use Claude.",
                  pad_l + 10, pad_b + plot_h / 2 - 12, 10.0)
        else:
            bw = min(slot * 0.62, 22.0)
            for i, day in enumerate(keys):
                pct = min(max(daily[day], 0.0), 100.0)
                bh  = max(plot_h * pct / 100.0, 2.0)
                x   = pad_l + slot * i + (slot - bw) / 2.0
                cls._fill_color(pct).setFill()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    ((x, pad_b), (bw, bh)), 2.5, 2.5
                ).fill()

            # Date labels at a readable interval, always including the latest day.
            step = max(1, n // 8)
            for idx in sorted(set(range(0, n, step)) | {n - 1}):
                cx = pad_l + slot * idx + slot / 2.0
                label(_fmt_day(keys[idx], "%d %b"), cx - 14, 6)

        # Pro-equivalent reference line — its height adapts to the plan tier
        # (≈5% on Max 20×, ≈20% on Max 5×). Peaks under it would also have fit
        # on Pro; peaks above it are where a larger plan earns its keep.
        ry = pad_b + plot_h * min(max(pro_pct, 0.0), 100.0) / 100.0
        NSColor.systemRedColor().colorWithAlphaComponent_(0.85).setStroke()
        ref = NSBezierPath.bezierPath()
        ref.moveToPoint_((pad_l, ry)); ref.lineToPoint_((W - pad_r, ry))
        ref.setLineWidth_(1.2)
        ref.setLineDash_count_phase_([4.0, 3.0], 2, 0.0)
        ref.stroke()
        label(f"Pro ≈ {pro_pct:.0f}%", pad_l + 3, ry + 2, 8.5, NSColor.systemRedColor())

        img.unlockFocus()
        img.setTemplate_(False)
        return img

    def _dashboard_info(self, daily: dict) -> str:
        """Build the multi-line account / org / usage summary for the Dashboard."""
        cfg = self.cfg
        connected = bool(cfg.get("live_mode") and cfg.get("session_key"))
        lines: list = []

        if not connected:
            return ("Not connected.\n\n"
                    "Click the menu bar icon → Connect to Claude.ai… and paste your "
                    "session key to start tracking your usage.")

        who = "  ·  ".join(x for x in (cfg.get("account_name"),
                                       cfg.get("account_email")) if x)
        if who:
            lines.append(who)

        meta = [f"Plan: {cfg.get('plan') or '—'}"]
        if cfg.get("org_role"): meta.append(f"Role: {cfg['org_role']}")
        if cfg.get("org_name"): meta.append(f"Org: {cfg['org_name']}")
        lines.append("   ·   ".join(meta))

        if cfg.get("team_seats"):
            lines.append(f"Seats: {cfg['team_seats']}")
        elif cfg.get("org_role") and (cfg.get("org_count") or 0) > 1:
            # Only for org-context (Team/Enterprise) users, not personal plans.
            lines.append(f"Member of {cfg['org_count']} organizations")

        # One metric per line — keeps each line short so nothing wraps oddly.
        usage = []
        u5, u7 = cfg.get("utilization_5h"), cfg.get("utilization_7d")
        if u5 is not None:
            cd = countdown(cfg.get("reset_time"))
            usage.append(f"5-hour:  {u5:.0f}%" + (f"   ·  resets in {cd}" if cd else ""))
        if u7 is not None:
            cd7 = countdown(cfg.get("reset_7d"))
            usage.append(f"7-day:  {u7:.0f}%" + (f"   ·  resets in {cd7}" if cd7 else ""))
        opus, son = cfg.get("utilization_opus_7d"), cfg.get("utilization_sonnet_7d")
        if opus is not None: usage.append(f"7-day Opus:  {opus:.0f}%")
        if son  is not None: usage.append(f"7-day Sonnet:  {son:.0f}%")
        if usage:
            lines.append("")
            lines.extend(usage)

        if not daily:
            lines.append("")
            lines.append("No usage history yet — your daily chart builds as you use Claude.")
        return "\n".join(lines)

    def _show_history_window(self, daily: dict) -> bool:
        """Native NSAlert dashboard: account/org/plan + usage + scrollable chart."""
        try:
            from AppKit import NSAlert, NSImageView, NSImage, NSScrollView
            from Foundation import NSMakeRect, NSMakePoint

            VIEW_W  = 448.0   # visible chart width inside the dialog
            plan    = self.cfg.get("plan") or "Claude"
            pro_pct = pro_threshold(plan)
            chart   = self._draw_history_chart(daily, pro_pct=pro_pct, min_width=VIEW_W)
            cw, ch  = chart.size().width, chart.size().height

            alert = NSAlert.alloc().init()
            alert.setMessageText_("Dashboard")
            alert.setInformativeText_(self._dashboard_info(daily))
            if ICON_PATH.exists():
                icon = NSImage.alloc().initWithContentsOfFile_(str(ICON_PATH))
                if icon:
                    alert.setIcon_(icon)

            if cw > VIEW_W + 1:
                # Wider than the window → horizontally scrollable, showing the
                # most-recent days first (scrolled to the right edge).
                scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, VIEW_W, ch))
                scroll.setHasHorizontalScroller_(True)
                scroll.setHasVerticalScroller_(False)
                scroll.setBorderType_(0)
                scroll.setDrawsBackground_(False)
                iv = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, cw, ch))
                iv.setImage_(chart)
                scroll.setDocumentView_(iv)
                clip = scroll.contentView()
                clip.scrollToPoint_(NSMakePoint(cw - VIEW_W, 0))
                scroll.reflectScrolledClipView_(clip)
                alert.setAccessoryView_(scroll)
            else:
                iv = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, cw, ch))
                iv.setImage_(chart)
                alert.setAccessoryView_(iv)

            alert.addButtonWithTitle_("Close")
            alert.runModal()
            return True
        except Exception:
            return False

    def show_history(self, _) -> None:
        # Ensure account/org metadata is loaded so the Dashboard is complete
        # (it's cached after the first sync, but fetch on demand if missing).
        key, org = self.cfg.get("session_key"), self.cfg.get("org_id")
        if key and org and not self.cfg.get("account_email"):
            ident = fetch_identity(key, org)
            if ident:
                self.cfg.update({k: v for k, v in ident.items() if v is not None})
                save_cfg(self.cfg)

        if self._show_history_window(daily_peaks(_read_history())):
            return
        rumps.alert(title="Dashboard", message=format_history_summary(), ok="Close")

    # ── Background tick ────────────────────────────────────────────────────

    @rumps.timer(60)
    def tick(self, _) -> None:
        self.cfg = load_cfg()
        if self.cfg.get("live_mode") and self.cfg.get("session_key"):
            self._do_live_fetch()
        else:
            pct, cd = _pct_and_cd(self.cfg)
            self._set_title(pct, cd)
            self._i_timer.title = f"  ↺  {cd}" if cd else "  ↺  —"


def _install_edit_menu(app) -> None:
    """Give the app an Edit menu so ⌘C / ⌘V / ⌘X / ⌘A work in text fields.

    Menu-bar (accessory) apps have no menu bar, so the standard clipboard
    shortcuts have nowhere to route — without this, paste only works via
    right-click. A minimal Edit menu wires up the key equivalents (no visible
    menu bar appears for an accessory app).
    """
    try:
        from AppKit import NSMenu, NSMenuItem
        main = NSMenu.alloc().init()
        container = NSMenuItem.alloc().init()
        main.addItem_(container)
        edit = NSMenu.alloc().initWithTitle_("Edit")
        for title, sel, key in (("Cut", "cut:", "x"), ("Copy", "copy:", "c"),
                                ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")):
            edit.addItem_(
                NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, key))
        container.setSubmenu_(edit)
        app.setMainMenu_(main)
    except Exception:
        pass


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(
        "\n"
        "  ┌──────────────────────────────────────────┐\n"
        "  │        Claude Usage Tracker  🟢           │\n"
        "  │  Monitoring your Claude.ai usage live.   │\n"
        "  │  Look for the icon in your menu bar ↗    │\n"
        "  └──────────────────────────────────────────┘\n"
        "\n"
        "  Press Ctrl-C to quit.\n"
        "\n"
        "  ☕  Like this tool? Support it on Buy Me a Coffee:\n"
        "      https://buymeacoffee.com/tinytoolkit\n"
    )

    try:
        try:
            # Rename the running bundle so macOS shows "Claude Statusbar"
            # (not "Python") in the Touch ID prompt and other system dialogs.
            from Foundation import NSBundle
            _b = NSBundle.mainBundle()
            _info = _b.localizedInfoDictionary() or _b.infoDictionary()
            if _info is not None:
                _info["CFBundleName"]        = APP_NAME
                _info["CFBundleDisplayName"] = APP_NAME
        except Exception:
            pass

        try:
            from AppKit import (NSApplication, NSImage,
                                NSApplicationActivationPolicyAccessory)
            app = NSApplication.sharedApplication()
            app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
            _install_edit_menu(app)   # enable ⌘C/⌘V in dialogs
            # Custom app icon → shown in every dialog (Connect, History, alerts).
            if ICON_PATH.exists():
                icon = NSImage.alloc().initWithContentsOfFile_(str(ICON_PATH))
                if icon:
                    app.setApplicationIconImage_(icon)
        except Exception:
            pass

        ClaudeTracker().run()
    except KeyboardInterrupt:
        print("\n  👋  Bye!\n")
        sys.exit(0)
    except Exception as e:
        log = Path.home() / ".claude_tracker_crash.log"
        log.write_text(f"[{datetime.now()}] CRASH: {e}\n{traceback.format_exc()}\n")
        print(f"\n❌  App crashed. Details: {log}\n{e}", file=sys.stderr)
        sys.exit(1)
